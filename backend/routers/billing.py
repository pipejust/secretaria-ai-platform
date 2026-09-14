"""Suscripción de cada empresa: plan, add-ons, checkout con Wompi y eventos.

- Administrador de empresa: ve su plan, contrata o renueva (widget de
  Wompi) y consulta pagos.
- Superadministrador: catálogo, llaves de Wompi y asignación manual.
- Wompi: `POST /api/webhook/wompi` con checksum; es la fuente de verdad.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from typing import Literal, Optional

from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, select

from database import get_session
from models import AddOn, MeetingSession, Payment, Plan, Subscription, Tenant, User
from routers.auth import get_current_tenant, require_admin, require_superadmin
from services import billing_catalog as cat
from services import entitlements as ent
from services import wompi

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["Suscripción"])
webhook_router = APIRouter(prefix="/api/webhook/wompi", tags=["Suscripción"])


def _ahora() -> datetime:
    return datetime.now()


def _frontend_url() -> str:
    return os.environ.get("FRONTEND_URL", "").rstrip("/")


def _sub(db: Session, tenant_id: int) -> Subscription | None:
    return db.exec(select(Subscription).where(Subscription.tenant_id == tenant_id)).first()


def _uso(db: Session, tenant_id: int, sub: Subscription | None) -> dict:
    inicio = None
    if sub and sub.current_period_start:
        try:
            inicio = datetime.fromisoformat(sub.current_period_start)
        except ValueError:
            inicio = None
    if inicio is None:
        inicio = _ahora().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    reuniones = db.exec(
        select(MeetingSession).where(
            MeetingSession.tenant_id == tenant_id,
            MeetingSession.created_at >= inicio.isoformat(),
        )
    ).all()
    usuarios = db.exec(select(User).where(User.tenant_id == tenant_id, User.is_active == True)).all()  # noqa: E712
    return {"meetings_this_period": len(reuniones), "active_users": len(usuarios),
            "period_start": inicio.isoformat()}


def _estado(db: Session, tenant_id: int) -> dict:
    sub = _sub(db, tenant_id)
    e = ent.de_empresa(db, tenant_id)
    plan = db.get(Plan, sub.plan_key) if sub else None
    return {
        "entitlements": e.dict(),
        "plan": cat.plan_dict(plan) if plan else None,
        "subscription": None if not sub else {
            "status": sub.status, "billing_mode": sub.billing_mode,
            "current_period_start": sub.current_period_start,
            "current_period_end": sub.current_period_end,
            "cancel_at_period_end": sub.cancel_at_period_end,
            "addons": json.loads(sub.addons_json or "[]"),
            "customer_email": sub.customer_email,
        },
        "usage": _uso(db, tenant_id, sub),
        "grace_days": ent.DIAS_GRACIA,
        "trial_days": cat.DIAS_PRUEBA,
        "payments_enabled": wompi.leer_config(db).configured,
    }


# ─────────────────────────── empresa (admin) ───────────────────────────


@router.get("/catalog")
def get_catalog(_u: User = Depends(require_admin), db: Session = Depends(get_session)):
    return cat.catalogo(db)


@router.get("/me")
def get_me(tenant: Tenant = Depends(get_current_tenant), _u: User = Depends(require_admin),
           db: Session = Depends(get_session)):
    return _estado(db, tenant.id)


class CheckoutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_key: str = Field(max_length=32)
    addons: list[str] = Field(default_factory=list, max_length=20)
    months: int = Field(default=1, ge=1, le=12)
    customer_email: Optional[str] = Field(default=None, max_length=320)


def _validar_seleccion(db: Session, plan_key: str, addons: list[str]) -> tuple[Plan, list[AddOn]]:
    plan = db.get(Plan, plan_key)
    if not plan or not plan.is_active or not plan.is_public:
        raise HTTPException(422, "Plan no disponible para contratación en línea")
    elegidos: list[AddOn] = []
    for key in dict.fromkeys(addons):
        a = db.get(AddOn, key)
        if not a or not a.is_active:
            raise HTTPException(422, f"Add-on desconocido: {key}")
        elegidos.append(a)
    claves = {a.key for a in elegidos}
    for a in elegidos:
        for req in json.loads(a.requires_json or "[]"):
            if req not in claves:
                raise HTTPException(422, f"«{a.name}» requiere «{req}»")
    return plan, elegidos


@router.post("/checkout", status_code=201)
def checkout(body: CheckoutIn, user: User = Depends(require_admin),
             tenant: Tenant = Depends(get_current_tenant), db: Session = Depends(get_session)):
    """Crea el pago pendiente y devuelve los parámetros del widget de Wompi.

    La firma de integridad se calcula aquí con el secreto; el navegador
    solo recibe la llave pública, la referencia, el monto y la firma.
    """
    cfg = wompi.leer_config(db)
    if not cfg.configured:
        raise HTTPException(503, "Los pagos en línea no están configurados todavía")
    plan, addons = _validar_seleccion(db, body.plan_key, body.addons)
    total = (plan.price_cop_cents + sum(a.price_cop_cents for a in addons)) * body.months
    if total <= 0:
        raise HTTPException(422, "El plan elegido no tiene precio en COP; contacta a Acten")
    reference = f"acten-{tenant.id}-{uuid.uuid4().hex[:12]}"
    pago = Payment(
        tenant_id=tenant.id, reference=reference, plan_key=plan.key,
        addons_json=json.dumps([a.key for a in addons]), months=body.months,
        amount_in_cents=total, currency="COP", created_by=user.id,
        customer_email=body.customer_email or user.email,
    )
    db.add(pago)
    db.commit()
    return {
        "reference": reference,
        "amount_in_cents": total,
        "currency": "COP",
        "public_key": cfg.public_key,
        "signature_integrity": wompi.firma_integridad(reference, total, "COP", cfg.integrity_secret),
        "redirect_url": f"{_frontend_url()}/admin/billing?reference={reference}",
        "customer_email": pago.customer_email,
        "widget_script": wompi.WIDGET_SCRIPT,
        "checkout_url": wompi.CHECKOUT_URL,
        "environment": cfg.environment,
        "summary": {"plan": cat.plan_dict(plan), "addons": [cat.addon_dict(a) for a in addons],
                    "months": body.months},
    }


def _pago_dict(p: Payment) -> dict:
    return {
        "reference": p.reference, "plan_key": p.plan_key, "addons": json.loads(p.addons_json or "[]"),
        "months": p.months, "amount_in_cents": p.amount_in_cents, "currency": p.currency,
        "status": p.status, "wompi_transaction_id": p.wompi_transaction_id,
        "payment_method": p.payment_method, "created_at": p.created_at, "approved_at": p.approved_at,
    }


@router.get("/payments")
def list_payments(tenant: Tenant = Depends(get_current_tenant), _u: User = Depends(require_admin),
                  db: Session = Depends(get_session)):
    rows = db.exec(select(Payment).where(Payment.tenant_id == tenant.id)
                   .order_by(Payment.id.desc()).limit(50)).all()
    return {"items": [_pago_dict(p) for p in rows]}


@router.get("/payments/{reference}")
def get_payment(reference: str, tenant: Tenant = Depends(get_current_tenant),
                _u: User = Depends(require_admin), db: Session = Depends(get_session)):
    """Estado de un pago. Si sigue pendiente y llegó el id de transacción de
    la redirección, se pregunta a Wompi: el evento puede tardar."""
    p = db.exec(select(Payment).where(Payment.reference == reference,
                                      Payment.tenant_id == tenant.id)).first()
    if not p:
        raise HTTPException(404, "Pago no encontrado")
    return _pago_dict(p)


class SyncIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(min_length=6, max_length=64)


@router.post("/payments/{reference}/sync")
def sync_payment(reference: str, body: SyncIn, tenant: Tenant = Depends(get_current_tenant),
                 _u: User = Depends(require_admin), db: Session = Depends(get_session)):
    p = db.exec(select(Payment).where(Payment.reference == reference,
                                      Payment.tenant_id == tenant.id)).first()
    if not p:
        raise HTTPException(404, "Pago no encontrado")
    if p.status == "approved":
        return _pago_dict(p)
    data = wompi.consultar_transaccion(wompi.leer_config(db), body.transaction_id)
    if not data or data.get("reference") != reference:
        raise HTTPException(409, "Wompi no confirma esa transacción para esta referencia")
    aplicar_transaccion(db, data, raw={"source": "sync", "data": data})
    db.refresh(p)
    return _pago_dict(p)


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cancel_at_period_end: bool = True


@router.post("/cancel")
def cancel(body: CancelIn, tenant: Tenant = Depends(get_current_tenant),
           _u: User = Depends(require_admin), db: Session = Depends(get_session)):
    sub = _sub(db, tenant.id)
    if not sub:
        raise HTTPException(404, "La empresa no tiene suscripción")
    sub.cancel_at_period_end = body.cancel_at_period_end
    sub.updated_at = _ahora().isoformat()
    db.add(sub)
    db.commit()
    return _estado(db, tenant.id)


# ─────────────────────────── aplicar pagos ───────────────────────────


def _sumar_meses(desde: datetime, meses: int) -> datetime:
    return desde + relativedelta(months=meses)


def activar_por_pago(db: Session, pago: Payment) -> Subscription:
    """Activa o extiende la suscripción según lo pagado. Idempotente por pago."""
    ahora = _ahora()
    sub = _sub(db, pago.tenant_id)
    inicio = ahora
    if sub and sub.current_period_end:
        try:
            fin_actual = datetime.fromisoformat(sub.current_period_end)
            if fin_actual > ahora and sub.plan_key == pago.plan_key:
                inicio = fin_actual  # renovación anticipada: se suma al final
        except ValueError:
            pass
    fin = _sumar_meses(inicio, pago.months)
    if not sub:
        sub = Subscription(tenant_id=pago.tenant_id, plan_key=pago.plan_key)
        db.add(sub)
    sub.plan_key = pago.plan_key
    sub.addons_json = pago.addons_json
    sub.status = "active"
    sub.billing_mode = "wompi"
    sub.current_period_start = ahora.isoformat() if inicio == ahora else sub.current_period_start
    sub.current_period_end = fin.isoformat()
    sub.cancel_at_period_end = False
    sub.customer_email = pago.customer_email or sub.customer_email
    sub.updated_at = ahora.isoformat()
    db.add(sub)
    return sub


ESTADOS = {"APPROVED": "approved", "DECLINED": "declined", "VOIDED": "voided", "ERROR": "error"}


def aplicar_transaccion(db: Session, tx: dict, raw: dict) -> Payment | None:
    reference = str(tx.get("reference") or "")
    pago = db.exec(select(Payment).where(Payment.reference == reference)).first()
    if not pago:
        logger.warning("Wompi: referencia desconocida %s", reference)
        return None
    estado = ESTADOS.get(str(tx.get("status") or "").upper())
    if estado is None:
        return pago  # PENDING u otros: nada que aplicar todavía
    if pago.status == "approved":
        return pago  # ya aplicado; evento repetido
    pago.wompi_transaction_id = str(tx.get("id") or pago.wompi_transaction_id or "")
    pago.payment_method = str(tx.get("payment_method_type") or "")[:32] or pago.payment_method
    pago.raw_event_json = json.dumps(raw)[:20000]
    if estado == "approved":
        if int(tx.get("amount_in_cents") or 0) != pago.amount_in_cents or \
                str(tx.get("currency") or "") != pago.currency:
            logger.error("Wompi: monto/moneda no coinciden en %s", reference)
            pago.status = "error"
            db.add(pago)
            db.commit()
            return pago
        pago.status = "approved"
        pago.approved_at = _ahora().isoformat()
        activar_por_pago(db, pago)
    else:
        pago.status = estado
    db.add(pago)
    db.commit()
    return pago


@webhook_router.post("")
async def wompi_event(request: Request, db: Session = Depends(get_session)):
    """Eventos de Wompi. Siempre 200 tras verificar; si no verifica, 401."""
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(400, "JSON inválido")
    cfg = wompi.leer_config(db)
    if not wompi.verificar_evento(body if isinstance(body, dict) else {}, cfg.events_secret):
        raise HTTPException(401, "Firma del evento inválida")
    if body.get("event") == "transaction.updated":
        tx = (body.get("data") or {}).get("transaction") or {}
        aplicar_transaccion(db, tx, raw=body)
    return {"received": True}


# ─────────────────────────── superadministrador ───────────────────────────


@router.get("/wompi-config")
def get_wompi_config(_u: User = Depends(require_superadmin), db: Session = Depends(get_session)):
    return wompi.estado_config(db)


class WompiConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment: Literal["sandbox", "production"]
    public_key: str = Field(min_length=8, max_length=120)
    private_key: Optional[str] = Field(default=None, max_length=120)
    events_secret: Optional[str] = Field(default=None, max_length=120)
    integrity_secret: Optional[str] = Field(default=None, max_length=120)


@router.put("/wompi-config")
def put_wompi_config(body: WompiConfigIn, _u: User = Depends(require_superadmin),
                     db: Session = Depends(get_session)):
    try:
        return wompi.guardar_config(db, **body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/catalog/all")
def get_catalog_all(_u: User = Depends(require_superadmin), db: Session = Depends(get_session)):
    return cat.catalogo(db, incluir_privados=True)


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=80)
    price_usd_cents: Optional[int] = Field(default=None, ge=0)
    price_cop_cents: Optional[int] = Field(default=None, ge=0)
    features: Optional[list[str]] = None
    meetings_per_month: Optional[int] = Field(default=None, ge=0)
    users_included: Optional[int] = Field(default=None, ge=0)
    is_public: Optional[bool] = None
    is_active: Optional[bool] = None


@router.put("/catalog/plans/{key}")
def put_plan(key: str, body: PlanIn, _u: User = Depends(require_superadmin),
             db: Session = Depends(get_session)):
    p = db.get(Plan, key)
    if not p:
        raise HTTPException(404, "Plan no encontrado")
    datos = body.model_dump(exclude_none=True)
    if "features" in datos:
        desconocidas = set(datos["features"]) - set(cat.FEATURES)
        if desconocidas:
            raise HTTPException(422, f"Funciones desconocidas: {sorted(desconocidas)}")
        p.features_json = json.dumps(datos.pop("features"))
    for k, v in datos.items():
        setattr(p, k, v)
    db.add(p)
    db.commit()
    return cat.plan_dict(p)


class AddOnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = Field(default=None, max_length=80)
    price_usd_cents: Optional[int] = Field(default=None, ge=0)
    price_cop_cents: Optional[int] = Field(default=None, ge=0)
    is_active: Optional[bool] = None


@router.put("/catalog/addons/{key}")
def put_addon(key: str, body: AddOnIn, _u: User = Depends(require_superadmin),
              db: Session = Depends(get_session)):
    a = db.get(AddOn, key)
    if not a:
        raise HTTPException(404, "Add-on no encontrado")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    db.add(a)
    db.commit()
    return cat.addon_dict(a)


@router.get("/tenants")
def list_tenant_subscriptions(_u: User = Depends(require_superadmin),
                              db: Session = Depends(get_session)):
    out = []
    for t in db.exec(select(Tenant).order_by(Tenant.id)).all():
        e = ent.de_empresa(db, t.id)
        sub = _sub(db, t.id)
        out.append({
            "tenant_id": t.id, "slug": t.slug, "name": t.name,
            "plan": e.plan_key, "status": e.status, "addons": e.addons,
            "billing_mode": e.billing_mode, "period_end": e.period_end,
            "trial_ends": e.trial_ends, "meeting_source": getattr(t, "meeting_source", "fireflies"),
            "notes": sub.notes if sub else "",
        })
    return {"items": out}


class AssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_key: str = Field(max_length=32)
    addons: list[str] = Field(default_factory=list, max_length=20)
    status: Literal["active", "cancelled"] = "active"
    current_period_end: Optional[str] = None  # ISO o null = sin vencimiento
    notes: str = Field(default="", max_length=2000)


@router.put("/tenants/{tenant_id}")
def assign_subscription(tenant_id: int, body: AssignIn, _u: User = Depends(require_superadmin),
                        db: Session = Depends(get_session)):
    """Asignación manual (contratos, cortesías, Enterprise)."""
    if not db.get(Tenant, tenant_id):
        raise HTTPException(404, "Empresa no encontrada")
    plan = db.get(Plan, body.plan_key)
    if not plan or not plan.is_active:
        raise HTTPException(422, "Plan desconocido")
    for key in body.addons:
        if not db.get(AddOn, key):
            raise HTTPException(422, f"Add-on desconocido: {key}")
    if body.current_period_end:
        try:
            datetime.fromisoformat(body.current_period_end)
        except ValueError:
            raise HTTPException(422, "current_period_end debe ser ISO 8601")
    sub = _sub(db, tenant_id) or Subscription(tenant_id=tenant_id, plan_key=body.plan_key,
                                              current_period_start=_ahora().isoformat())
    sub.plan_key = body.plan_key
    sub.addons_json = json.dumps(list(dict.fromkeys(body.addons)))
    sub.status = body.status
    sub.billing_mode = "manual"
    sub.current_period_end = body.current_period_end
    sub.notes = body.notes
    sub.updated_at = _ahora().isoformat()
    db.add(sub)
    db.commit()
    return _estado(db, tenant_id)
