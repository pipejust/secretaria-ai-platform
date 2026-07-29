"""Trazabilidad de pendientes (action_items) a nivel global.

Endpoints:
  GET  /api/pendientes              → tareas filtradas por status / vencimiento / proyecto / responsable
  GET  /api/pendientes/stats        → resumen agregado para el dashboard
  PATCH /api/pendientes/{id}/status → cambia status (pending|done|blocked|cancelled)
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Project, Role, Tenant, User
from routers.auth import get_current_tenant, get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/pendientes", tags=["Pendientes / Trazabilidad"])

VALID_STATUSES = {"pending", "done", "blocked", "cancelled"}


def _parse_due(due: Optional[str]) -> Optional[datetime]:
    """Parsea due_date en cualquier formato razonable. Devuelve None si no se puede."""
    if not due:
        return None
    s = str(due).strip()
    if not s:
        return None
    # ISO con T
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    # Solo fecha YYYY-MM-DD
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except ValueError:
        pass
    return None


def _classify(item: ActionItem, now: datetime) -> str:
    """vencido / proximo / sin_fecha / completado / cancelado / bloqueado."""
    if item.status == "done":
        return "completado"
    if item.status == "cancelled":
        return "cancelado"
    if item.status == "blocked":
        return "bloqueado"
    due = _parse_due(item.due_date)
    if not due:
        return "sin_fecha"
    if due < now:
        return "vencido"
    if (due - now).days <= 7:
        return "proximo"
    return "pendiente"


def _miembros_por_proyecto(
    db: Session, tenant_id: int,
) -> Dict[int, tuple[set[str], list[set[str]]]]:
    """project_id → (correos, nombres en tokens) de sus integrantes.

    Se calcula una vez por petición: preguntarlo por tarea serían cientos
    de consultas para pintar una lista.
    """
    from models import ProjectContact
    from services.servicios_sync import name_tokens, norm_email

    filas = db.exec(
        select(ProjectContact, Project.id)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == tenant_id)
    ).all()
    fuera: Dict[int, tuple[set[str], list[set[str]]]] = {}
    for contacto, pid in filas:
        correos, nombres = fuera.setdefault(pid, (set(), []))
        em = norm_email(contacto.email)
        if em:
            correos.add(em)
        toks = name_tokens(contacto.name)
        if toks:
            nombres.append(toks)
    return fuera


def _tiene_responsable(item: ActionItem) -> bool:
    """¿La tarea tiene un responsable identificable?

    La lista de marcadores vive en `services.owners`: la misma pregunta se
    hace aquí, en la API pública y en el calendario, y tres copias del
    literal es una que alguien traducirá algún día.
    """
    from services.owners import tiene_responsable

    return tiene_responsable(item.owner_name, item.owner_email)


def _es_del_proyecto(
    item: ActionItem, miembros: Optional[tuple[set[str], list[set[str]]]],
) -> bool:
    """¿El responsable de la tarea es integrante del proyecto?

    Devuelve `False` **solo cuando no hay ningún indicio** de que lo sea.
    El sesgo es deliberado: este dato alimenta un interruptor que oculta
    filas, y esconder el trabajo de alguien del equipo es peor error que
    dejar visible el de alguien de fuera.

    Por eso el correo no decide solo. Una misma persona figura con
    correos distintos según el proyecto —Felipe está como `@nexura.com`
    en el del cliente y como `@softnexus.io` en el interno— así que si el
    correo no cuadra se sigue mirando el nombre en vez de darlo por
    ajeno.

    Con el nombre, dos palabras bastan. Con una sola —«William», tal cual
    lo dejó la transcripción— vale si encaja con algún integrante: es
    ambiguo, y ante la duda se muestra.
    """
    if not miembros:
        return False
    from services.servicios_sync import name_tokens, norm_email

    correos, nombres = miembros
    em = norm_email(item.owner_email)
    if em and em in correos:
        return True

    toks = name_tokens(item.owner_name)
    if not toks:
        return False
    if any(len(toks & n) >= 2 for n in nombres):
        return True
    if len(toks) == 1:
        return any(toks & n for n in nombres)
    return False


def _serialize(
    item: ActionItem,
    project_name: str,
    now: datetime,
    user_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    tenant_name: str = "",
    owner_in_project: Optional[bool] = None,
) -> Dict[str, Any]:
    """Serializa un ActionItem para respuesta API.

    `user_meta` es un dict opcional `{email_lower: UserSummary}` —
    cuando el `owner_email` matchea un User del tenant, devolvemos también
    `owner_user_id`, `owner_full_name` (nombre EDITADO por el usuario en su
    perfil, no el que detectó la IA), `owner_avatar_url`, rol y departamento.
    """
    meta: Dict[str, Any] = {}
    if user_meta and item.owner_email:
        meta = user_meta.get((item.owner_email or "").strip().lower(), {}) or {}
    # Nombre display: si el email matchea un user, ese es la fuente de verdad;
    # si no, el nombre extraído por IA al procesar la reunión.
    display_name = meta.get("full_name") or item.owner_name or ""
    return {
        "id": item.id,
        "session_id": item.session_id,
        "title": item.title,
        "description": item.description,
        "owner_name": item.owner_name,
        "owner_email": item.owner_email,
        # Resolución a User del tenant (None si es contacto externo).
        "owner_user_id":    meta.get("id"),
        "owner_full_name":  display_name,
        "owner_avatar_url": meta.get("avatar_url"),
        "owner_role":       meta.get("role") or "",
        "owner_department": meta.get("department") or "",
        "owner_position":   meta.get("position") or "",
        "owner_is_user":    bool(meta.get("id")),
        "owner_company":    tenant_name,
        "due_date": item.due_date,
        "due_time": getattr(item, "due_time", None),
        "priority": (getattr(item, "priority", None) or "media").lower(),
        "status": item.status,
        "completed_at": item.completed_at,
        "is_approved": item.is_approved,
        "external_id": item.external_id,
        "project_name": project_name,
        # ¿El responsable figura entre los integrantes del proyecto?
        # `None` cuando la tarea no cuelga de ningún proyecto y la
        # pregunta no tiene sentido.
        "owner_in_project": owner_in_project,
        "bucket": _classify(item, now),
    }


@router.get("")
def list_pendientes(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
    bucket: Optional[str] = Query(
        None,
        description="vencido | proximo | sin_fecha | pendiente | completado | bloqueado | cancelado | activos",
    ),
    project_id: Optional[int] = Query(None),
    owner: Optional[str] = Query(None, description="Texto a buscar en owner_name/email"),
    priority: Optional[str] = Query(None, description="alta | media | baja"),
    externos: bool = Query(
        True,
        description=(
            "Incluir tareas cuyo responsable NO figura entre los "
            "integrantes del proyecto. En false se ocultan."
        ),
    ),
    limit: int = Query(500, ge=1, le=2000),
):
    """Lista action_items del TENANT actual con su clasificación temporal.

    Aislamiento estricto: solo devuelve items con `tenant_id == current_tenant.id`.
    """
    now = datetime.now()

    stmt = select(ActionItem).where(ActionItem.tenant_id == tenant.id)
    if project_id is not None:
        stmt = stmt.join(MeetingSession, MeetingSession.id == ActionItem.session_id).where(
            MeetingSession.project_id == project_id
        )
    items = db.exec(stmt.limit(limit * 4)).all()

    # Cache de proyectos del tenant
    project_names: Dict[int, str] = {
        p.id: p.name
        for p in db.exec(select(Project).where(Project.tenant_id == tenant.id)).all()
        if p.id
    }

    # session_id → project_id, solo de sesiones del tenant
    session_to_project = {
        s.id: s.project_id
        for s in db.exec(
            select(MeetingSession).where(MeetingSession.tenant_id == tenant.id)
        ).all()
        if s.id is not None
    }

    # Carga de usuarios del tenant — resolver centralizado.
    # 1) Match por email (más confiable)
    # 2) Match por NOMBRE solo si es UNÍVOCO en el tenant (fallback seguro
    #    cuando la tarea solo tiene owner_name de Fireflies). Tageamos el
    #    email real del user para que el resto del pipeline sea email-only.
    from services import user_resolver
    emails_in_use = list({(i.owner_email or "").strip().lower() for i in items if i.owner_email})
    user_meta = user_resolver.resolve_emails(db, tenant.id, emails_in_use)
    names_in_use = list({(i.owner_name or "").strip() for i in items if (i.owner_name and not i.owner_email)})
    name_meta = user_resolver.resolve_names_unambiguous(db, tenant.id, names_in_use) if names_in_use else {}
    # Inyectamos: si una tarea no tiene owner_email pero su nombre matchea
    # unívocamente a un user del tenant, ponemos su email en user_meta para
    # que el _serialize lo encuentre por email_lower.
    for i in items:
        if i.owner_email:
            continue
        if not i.owner_name:
            continue
        nm = user_resolver._normalize_name(i.owner_name)
        if nm and nm in name_meta:
            u = name_meta[nm]
            email_key = u["email"].strip().lower()
            user_meta[email_key] = u
            # parche transitorio en el objeto in-memory para que _serialize
            # use ese email al lookupear (no se persiste a la BD).
            i.owner_email = u["email"]
    tenant_name = tenant.name or ""
    miembros = _miembros_por_proyecto(db, tenant.id)

    out: List[Dict[str, Any]] = []
    for item in items:
        proj_id = session_to_project.get(item.session_id)
        proj_name = project_names.get(proj_id, "General") if proj_id else "General"
        # Sin proyecto o sin responsable, la pregunta no tiene sentido y se
        # deja en `None`: así el interruptor no esconde las tareas que
        # están esperando dueño, que son justo las que hay que ver.
        en_proyecto = (
            _es_del_proyecto(item, miembros.get(proj_id))
            if (proj_id and _tiene_responsable(item))
            else None
        )
        if externos is False and en_proyecto is False:
            continue
        record = _serialize(
            item, proj_name, now, user_meta=user_meta, tenant_name=tenant_name,
            owner_in_project=en_proyecto,
        )

        if bucket:
            wanted = bucket.lower()
            if wanted == "activos":
                if record["bucket"] in ("completado", "cancelado"):
                    continue
            elif record["bucket"] != wanted:
                continue
        if owner:
            o = owner.lower()
            if o not in (record["owner_name"] or "").lower() and o not in (record["owner_email"] or "").lower():
                continue
        if priority:
            p = priority.lower().strip()
            if p and (record.get("priority") or "media").lower() != p:
                continue
        out.append(record)
        if len(out) >= limit:
            break

    # Ordenar: vencidos primero (por due_date asc), luego próximos, luego sin fecha
    bucket_order = {
        "vencido": 0,
        "proximo": 1,
        "pendiente": 2,
        "bloqueado": 3,
        "sin_fecha": 4,
        "completado": 5,
        "cancelado": 6,
    }
    out.sort(
        key=lambda r: (
            bucket_order.get(r["bucket"], 99),
            r["due_date"] or "9999-12-31",
        )
    )
    return {"items": out, "total": len(out)}


@router.get("/stats")
def stats(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Resumen agregado para tarjetas del dashboard (tenant-scoped)."""
    now = datetime.now()
    counters = {b: 0 for b in (
        "vencido", "proximo", "pendiente", "sin_fecha",
        "bloqueado", "completado", "cancelado",
    )}
    by_owner: Dict[str, int] = {}

    items = db.exec(select(ActionItem).where(ActionItem.tenant_id == tenant.id)).all()
    for item in items:
        bucket_name = _classify(item, now)
        counters[bucket_name] += 1
        if bucket_name in ("vencido", "proximo", "pendiente", "bloqueado"):
            owner = (item.owner_name or "Sin asignar").strip() or "Sin asignar"
            by_owner[owner] = by_owner.get(owner, 0) + 1

    top_owners = sorted(by_owner.items(), key=lambda kv: -kv[1])[:10]
    return {
        "counts": counters,
        "active_total": (
            counters["vencido"] + counters["proximo"]
            + counters["pendiente"] + counters["bloqueado"] + counters["sin_fecha"]
        ),
        "top_owners_active": [
            {"owner": o, "count": c} for o, c in top_owners
        ],
    }


class StatusUpdate(BaseModel):
    status: str


@router.patch("/{item_id}/status")
def update_status(
    item_id: int,
    payload: StatusUpdate,
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_current_tenant),
):
    if payload.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Status inválido. Usa uno de: {sorted(VALID_STATUSES)}",
        )
    item = db.get(ActionItem, item_id)
    # Aislamiento: 404 si el item es de otra empresa.
    if not item or item.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Action item no encontrado")
    item.status = payload.status
    item.completed_at = (
        datetime.now().isoformat() if payload.status == "done" else None
    )
    item.updated_at = datetime.now().isoformat()
    db.add(item)
    db.commit()
    db.refresh(item)

    # El cambio hecho desde Acten también avisa a Servicios: si no, su
    # tablero mostraría un estado viejo hasta el siguiente refresco manual.
    try:
        from services.webhook_sender import send_event_bg
        send_event_bg("task.updated", {
            "task_id": item.id,
            "status": item.status,
            "source": "acten",
        }, tenant_id=item.tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("webhook task.updated (%s) no enviado: %s", item.id, exc)

    return {"id": item.id, "status": item.status, "completed_at": item.completed_at}
