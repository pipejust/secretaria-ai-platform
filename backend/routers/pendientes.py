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


def _serialize(
    item: ActionItem,
    project_name: str,
    now: datetime,
    user_meta: Optional[Dict[str, Dict[str, Any]]] = None,
    tenant_name: str = "",
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

    out: List[Dict[str, Any]] = []
    for item in items:
        proj_id = session_to_project.get(item.session_id)
        proj_name = project_names.get(proj_id, "General") if proj_id else "General"
        record = _serialize(item, proj_name, now, user_meta=user_meta, tenant_name=tenant_name)

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
    db.add(item)
    db.commit()
    db.refresh(item)
    return {"id": item.id, "status": item.status, "completed_at": item.completed_at}
