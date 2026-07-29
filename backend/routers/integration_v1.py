"""API pública v1 — integración con la plataforma de Servicios/RRHH.

Implementa el contrato de `docs/INTEGRACION_ACTEN_RRHH.md`.

Autenticación: `X-API-Key` (identifica tenant + scopes) y
`X-On-Behalf-Of` (UUID del empleado en cuyo nombre se actúa).

Modelo de permisos acordado: **un empleado ve las reuniones y tareas de
los proyectos donde es miembro**. La resolución vive en
`services.api_key_auth._resolve_visibility`.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database import get_session
from models import ActionItem, MeetingSession, Project, ProjectContact
from services.api_key_auth import IntegrationContext, require_scopes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Integración v1 (API pública)"])

# ── Máquina de estados de una tarea (contrato §7) ──────────────────────
TASK_STATES = ("pending", "blocked", "done", "cancelled")
VALID_TRANSITIONS: dict[str, set[str]] = {
    "pending":   {"blocked", "done", "cancelled"},
    "blocked":   {"pending", "done", "cancelled"},
    "done":      {"pending"},          # reabrir
    "cancelled": set(),                # terminal
}


def _now() -> str:
    return datetime.now().isoformat()


def _project_ref_map(db: Session, tenant_id: int) -> dict[int, Optional[str]]:
    """project_id interno → external_ref (UUID del sistema externo)."""
    rows = db.exec(
        select(Project.id, Project.external_ref).where(Project.tenant_id == tenant_id)
    ).all()
    return {r[0]: r[1] for r in rows}


def _resolve_project(
    db: Session, tenant_id: int, external_ref: str,
) -> Project:
    proj = db.exec(
        select(Project)
        .where(Project.tenant_id == tenant_id)
        .where(Project.external_ref == external_ref)
    ).first()
    if not proj:
        raise HTTPException(422, f"No existe un proyecto sincronizado con id '{external_ref}'.")
    return proj


def _owner_block(db: Session, item: ActionItem) -> dict[str, Any]:
    """Bloque `owner` de una tarea, con el UUID externo cuando se conoce."""
    ext = None
    if item.owner_email:
        row = db.exec(
            select(ProjectContact.external_ref)
            .where(ProjectContact.email == item.owner_email)
            .where(ProjectContact.external_ref.is_not(None))
        ).first()
        ext = row[0] if isinstance(row, tuple) else row
    return {
        "employee_external_id": ext,
        "name": item.owner_name or "",
        "email": item.owner_email or "",
    }


def _serialize_task(
    db: Session, item: ActionItem, proj_refs: dict[int, Optional[str]],
    session_project: dict[int, Optional[int]],
) -> dict[str, Any]:
    pid = session_project.get(item.session_id)
    return {
        "id": item.id,
        "title": item.title or "",
        "description": item.description or "",
        "status": item.status or "pending",
        "priority": item.priority or "media",
        "owner": _owner_block(db, item),
        "due_date": item.due_date,
        "due_time": item.due_time,
        "source_session_id": item.session_id,
        "project_external_id": proj_refs.get(pid) if pid else None,
        "origin": item.origin or "meeting",
        "column": item.kanban_column,
        "order": item.kanban_order,
        "created_at": getattr(item, "created_at", None),
        "updated_at": item.updated_at,
    }


def _visible_session_ids(
    db: Session, ctx: IntegrationContext,
) -> Optional[list[int]]:
    """Sesiones que el empleado puede ver. `None` = sin restricción."""
    if not ctx.on_behalf_of:
        return None
    if not ctx.visible_project_ids:
        return []
    rows = db.exec(
        select(MeetingSession.id)
        .where(MeetingSession.tenant_id == ctx.tenant.id)
        .where(MeetingSession.project_id.in_(ctx.visible_project_ids))
    ).all()
    return [r[0] if isinstance(r, tuple) else r for r in rows]


# ══════════════════════════════════════════════════════════════════════
# SESIONES
# ══════════════════════════════════════════════════════════════════════

@router.get("/sessions")
def list_sessions(
    project_external_id: Optional[str] = None,
    updated_since: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Reuniones visibles para el empleado (proyectos donde es miembro)."""
    q = select(MeetingSession).where(MeetingSession.tenant_id == ctx.tenant.id)

    if ctx.on_behalf_of:
        if not ctx.visible_project_ids:
            return {"items": [], "total": 0, "page": page, "limit": limit}
        q = q.where(MeetingSession.project_id.in_(ctx.visible_project_ids))

    if project_external_id:
        proj = _resolve_project(db, ctx.tenant.id, project_external_id)
        q = q.where(MeetingSession.project_id == proj.id)
    if status_filter:
        q = q.where(MeetingSession.status == status_filter)
    else:
        q = q.where(MeetingSession.status != "archived")
    if search:
        q = q.where(MeetingSession.title.ilike(f"%{search}%"))
    if updated_since:
        q = q.where(MeetingSession.created_at >= updated_since)

    rows = db.exec(q.order_by(MeetingSession.id.desc())).all()
    total = len(rows)
    page_rows = rows[(page - 1) * limit: page * limit]
    proj_refs = _project_ref_map(db, ctx.tenant.id)

    items = []
    for s in page_rows:
        n_tasks = len(db.exec(
            select(ActionItem).where(ActionItem.session_id == s.id)
        ).all())
        items.append({
            "id": s.id,
            "title": s.title or "",
            "date": s.date,
            "project_external_id": proj_refs.get(s.project_id) if s.project_id else None,
            "status": s.status,
            "counts": {"tasks": n_tasks},
        })
    return {"items": items, "total": total, "page": page, "limit": limit}


@router.get("/sessions/{session_id}")
def get_session_detail(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    s = db.get(MeetingSession, session_id)
    if not s or s.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Sesión no encontrada.")
    if ctx.on_behalf_of and s.project_id not in ctx.visible_project_ids:
        raise HTTPException(404, "Sesión no encontrada.")

    proj_refs = _project_ref_map(db, ctx.tenant.id)
    tasks = db.exec(select(ActionItem).where(ActionItem.session_id == s.id)).all()
    sp = {s.id: s.project_id}

    import json as _json
    try:
        attendees = _json.loads(s.processed_attendees or "[]")
    except Exception:
        attendees = []

    return {
        "id": s.id,
        "title": s.title or "",
        "date": s.date,
        "project_external_id": proj_refs.get(s.project_id) if s.project_id else None,
        "status": s.status,
        "summary": s.raw_summary or "",
        "decisions": s.processed_decisions or "",
        "agreements": s.processed_agreements or "",
        "risks": s.processed_risks or "",
        "participants": attendees,
        "tasks": [_serialize_task(db, t, proj_refs, sp) for t in tasks],
    }


@router.get("/sessions/{session_id}/transcript")
def get_transcript(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    s = db.get(MeetingSession, session_id)
    if not s or s.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Sesión no encontrada.")
    if ctx.on_behalf_of and s.project_id not in ctx.visible_project_ids:
        raise HTTPException(404, "Sesión no encontrada.")
    return {"session_id": s.id, "transcript": s.raw_transcript or ""}


# ══════════════════════════════════════════════════════════════════════
# TAREAS
# ══════════════════════════════════════════════════════════════════════

@router.get("/tasks")
def list_tasks(
    project_external_id: Optional[str] = None,
    owner_external_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    updated_since: Optional[str] = None,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("tasks:read")),
):
    q = select(ActionItem).where(ActionItem.tenant_id == ctx.tenant.id)

    visible = _visible_session_ids(db, ctx)
    if visible is not None:
        if not visible:
            return {"items": [], "total": 0, "page": page, "limit": limit}
        q = q.where(ActionItem.session_id.in_(visible))

    if project_external_id:
        proj = _resolve_project(db, ctx.tenant.id, project_external_id)
        sids = db.exec(
            select(MeetingSession.id).where(MeetingSession.project_id == proj.id)
        ).all()
        sids = [r[0] if isinstance(r, tuple) else r for r in sids]
        q = q.where(ActionItem.session_id.in_(sids or [-1]))
    if status_filter:
        q = q.where(ActionItem.status == status_filter)
    if owner_external_id:
        emails = db.exec(
            select(ProjectContact.email)
            .where(ProjectContact.external_ref == owner_external_id)
        ).all()
        emails = [r[0] if isinstance(r, tuple) else r for r in emails]
        q = q.where(ActionItem.owner_email.in_(emails or ["__none__"]))
    if search:
        q = q.where(ActionItem.title.ilike(f"%{search}%"))
    if updated_since:
        q = q.where(ActionItem.updated_at >= updated_since)

    rows = db.exec(q.order_by(ActionItem.id.desc())).all()
    total = len(rows)
    page_rows = rows[(page - 1) * limit: page * limit]

    proj_refs = _project_ref_map(db, ctx.tenant.id)
    sess_proj = {
        (r[0] if isinstance(r, tuple) else r.id): (r[1] if isinstance(r, tuple) else r.project_id)
        for r in db.exec(
            select(MeetingSession.id, MeetingSession.project_id)
            .where(MeetingSession.tenant_id == ctx.tenant.id)
        ).all()
    }
    return {
        "items": [_serialize_task(db, t, proj_refs, sess_proj) for t in page_rows],
        "total": total, "page": page, "limit": limit,
    }


class TaskPatch(BaseModel):
    status: Optional[str] = None
    owner_external_id: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    priority: Optional[str] = None
    column: Optional[str] = None
    order: Optional[int] = None


@router.patch("/tasks/{task_id}")
def patch_task(
    task_id: int,
    payload: TaskPatch,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("tasks:write")),
):
    item = db.get(ActionItem, task_id)
    if not item or item.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Tarea no encontrada.")

    visible = _visible_session_ids(db, ctx)
    if visible is not None and item.session_id not in visible:
        raise HTTPException(404, "Tarea no encontrada.")

    data = payload.model_dump(exclude_unset=True)

    if "status" in data and data["status"] is not None:
        new = str(data["status"]).strip().lower()
        cur = (item.status or "pending").lower()
        if new not in TASK_STATES:
            raise HTTPException(422, f"Estado inválido. Válidos: {', '.join(TASK_STATES)}")
        if new != cur and new not in VALID_TRANSITIONS.get(cur, set()):
            raise HTTPException(
                409,
                f"Transición inválida: '{cur}' → '{new}'. La tarea está en '{cur}'.",
            )
        item.status = new
        item.completed_at = _now() if new == "done" else None

    if "owner_external_id" in data:
        ext = data["owner_external_id"]
        if ext:
            c = db.exec(
                select(ProjectContact).where(ProjectContact.external_ref == ext)
            ).first()
            if not c:
                raise HTTPException(422, f"No hay empleado sincronizado con id '{ext}'.")
            item.owner_name, item.owner_email = c.name or "", c.email or ""
        else:
            item.owner_name, item.owner_email = "", ""

    if "priority" in data and data["priority"]:
        p = str(data["priority"]).lower().strip()
        if p not in ("alta", "media", "baja"):
            raise HTTPException(422, "priority debe ser alta | media | baja.")
        item.priority = p

    for src, dst in (("title", "title"), ("description", "description"),
                     ("due_date", "due_date"), ("due_time", "due_time"),
                     ("column", "kanban_column"), ("order", "kanban_order")):
        if src in data:
            setattr(item, dst, data[src])

    item.updated_at = _now()
    db.add(item); db.commit(); db.refresh(item)

    proj_refs = _project_ref_map(db, ctx.tenant.id)
    s = db.get(MeetingSession, item.session_id)
    return _serialize_task(db, item, proj_refs, {item.session_id: s.project_id if s else None})


# ══════════════════════════════════════════════════════════════════════
# SINCRONIZACIÓN (PULL desde la plataforma de Servicios)
# ══════════════════════════════════════════════════════════════════════

@router.post("/sync/run")
def run_sync(
    dry_run: bool = Query(True, description="true = solo reporta, no escribe"),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sync:write")),
):
    """Trae empleados y proyectos de Servicios y los enlaza por UUID.

    `dry_run=true` (por defecto) reporta qué haría sin tocar la base.
    La incremental pide `status=all` a propósito — ver el módulo.
    """
    from services.servicios_sync import run_full_sync
    return run_full_sync(db, ctx.tenant.id, dry_run=dry_run)


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = ""
    project_external_id: Optional[str] = None
    source_session_id: Optional[int] = None
    owner_external_id: Optional[str] = None
    due_date: Optional[str] = None
    due_time: Optional[str] = None
    priority: str = "media"
    column: Optional[str] = None


@router.post("/tasks", status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("tasks:write")),
):
    if not payload.project_external_id and not payload.source_session_id:
        raise HTTPException(422, "Se requiere project_external_id o source_session_id.")

    session_id = payload.source_session_id
    if session_id:
        s = db.get(MeetingSession, session_id)
        if not s or s.tenant_id != ctx.tenant.id:
            raise HTTPException(422, "La sesión indicada no existe.")
    else:
        proj = _resolve_project(db, ctx.tenant.id, payload.project_external_id or "")
        s = db.exec(
            select(MeetingSession)
            .where(MeetingSession.project_id == proj.id)
            .order_by(MeetingSession.id.desc())
        ).first()
        if not s:
            raise HTTPException(
                422, "El proyecto aún no tiene sesiones; indica source_session_id.",
            )
        session_id = s.id

    owner_name = owner_email = ""
    if payload.owner_external_id:
        c = db.exec(
            select(ProjectContact)
            .where(ProjectContact.external_ref == payload.owner_external_id)
        ).first()
        if not c:
            raise HTTPException(
                422, f"No hay empleado sincronizado con id '{payload.owner_external_id}'.",
            )
        owner_name, owner_email = c.name or "", c.email or ""

    pr = (payload.priority or "media").lower().strip()
    if pr not in ("alta", "media", "baja"):
        pr = "media"

    item = ActionItem(
        tenant_id=ctx.tenant.id,
        session_id=session_id,
        title=payload.title.strip(),
        description=payload.description or "",
        owner_name=owner_name,
        owner_email=owner_email,
        due_date=payload.due_date,
        due_time=payload.due_time,
        priority=pr,
        status="pending",
        origin="manual",
        kanban_column=payload.column,
        is_approved=False,
        updated_at=_now(),
    )
    db.add(item); db.commit(); db.refresh(item)

    proj_refs = _project_ref_map(db, ctx.tenant.id)
    s2 = db.get(MeetingSession, item.session_id)
    return _serialize_task(db, item, proj_refs, {item.session_id: s2.project_id if s2 else None})
