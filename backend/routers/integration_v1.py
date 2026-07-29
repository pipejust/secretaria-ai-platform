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
from date_utils import is_valid_due_date, normalize_due_date
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


# Mismo tope en todos los listados. Antes `/tasks` y `/sessions` cortaban
# en 100 y `/calendar/events` en 200, sin más razón que el orden en que se
# escribieron. El riesgo no es el `422` —ése se ve enseguida— sino pedir
# una página y pintarla como si fuera todo; por eso las respuestas llevan
# además `has_more` y `pages`.
MAX_LIMIT = 200


def _paginar(items: list, page: int, limit: int) -> dict:
    total = len(items)
    trozo = items[(page - 1) * limit: page * limit]
    return {
        "items": trozo,
        "total": total,
        "page": page,
        "limit": limit,
        "pages": max(1, (total + limit - 1) // limit),
        "has_more": page * limit < total,
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


def _owner_block(db: Session, item: ActionItem) -> Optional[dict[str, Any]]:
    """Bloque `owner`, o **`None` si la tarea no tiene dueño**.

    Antes se devolvía `{"employee_external_id": null, "name": "Por
    asignar"}`, que desde fuera no se distingue de una persona real sin
    fichar — y en un proyecto de cliente ésas son la mayoría. Obligaba a
    quien consume a comparar el literal contra una lista, y ese código se
    rompe el día que alguien traduzca la interfaz.
    """
    from services.owners import limpiar, tiene_responsable

    if not tiene_responsable(item.owner_name, item.owner_email):
        return None

    correo = limpiar(item.owner_email)
    ext = None
    if correo:
        row = db.exec(
            select(ProjectContact.external_ref)
            .where(ProjectContact.email == correo)
            .where(ProjectContact.external_ref.is_not(None))
        ).first()
        ext = row[0] if isinstance(row, tuple) else row
    return {
        "employee_external_id": ext,
        "name": limpiar(item.owner_name) or "",
        "email": correo or "",
    }


def _serialize_task(
    db: Session, item: ActionItem, proj_refs: dict[int, Optional[str]],
    session_project: dict[int, Optional[int]],
) -> dict[str, Any]:
    pid = session_project.get(item.session_id)
    duenio = _owner_block(db, item)
    return {
        "id": item.id,
        "title": item.title or "",
        "description": item.description or "",
        "status": item.status or "pending",
        "priority": item.priority or "media",
        "owner": duenio,
        # Explícito además de `owner: null`, para quien prefiera un
        # booleano a comprobar la ausencia de un objeto.
        "unassigned": duenio is None,
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
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Reuniones visibles para el empleado (proyectos donde es miembro)."""
    q = select(MeetingSession).where(MeetingSession.tenant_id == ctx.tenant.id)

    if ctx.on_behalf_of:
        if not ctx.visible_project_ids:
            return _paginar([], page, limit)
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
    sobre = _paginar(rows, page, limit)
    sobre["items"] = items
    return sobre


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
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("tasks:read")),
):
    q = select(ActionItem).where(ActionItem.tenant_id == ctx.tenant.id)

    visible = _visible_session_ids(db, ctx)
    if visible is not None:
        if not visible:
            return _paginar([], page, limit)
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
    sobre = _paginar(rows, page, limit)
    sobre["items"] = [_serialize_task(db, t, proj_refs, sess_proj) for t in page_rows]
    return sobre


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

    if "due_date" in data:
        if not is_valid_due_date(data["due_date"]):
            raise HTTPException(422, "due_date debe ser YYYY-MM-DD o null.")
        data["due_date"] = normalize_due_date(data["due_date"])

    for src, dst in (("title", "title"), ("description", "description"),
                     ("due_date", "due_date"), ("due_time", "due_time"),
                     ("column", "kanban_column"), ("order", "kanban_order")):
        if src in data:
            setattr(item, dst, data[src])

    item.updated_at = _now()
    db.add(item); db.commit(); db.refresh(item)

    proj_refs = _project_ref_map(db, ctx.tenant.id)
    s = db.get(MeetingSession, item.session_id)
    out = _serialize_task(db, item, proj_refs, {item.session_id: s.project_id if s else None})

    # Aviso a Servicios. Best-effort: si falla, la tarea ya quedó guardada.
    from services.webhook_sender import send_event_bg
    send_event_bg("task.updated", {
        "task_id": item.id,
        "status": item.status,
        "project_external_id": out.get("project_external_id"),
        "owner_external_id": (out.get("owner") or {}).get("employee_external_id"),
    }, tenant_id=ctx.tenant.id)
    return out


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

    if not is_valid_due_date(payload.due_date):
        raise HTTPException(422, "due_date debe ser YYYY-MM-DD o null.")

    item = ActionItem(
        tenant_id=ctx.tenant.id,
        session_id=session_id,
        title=payload.title.strip(),
        description=payload.description or "",
        owner_name=owner_name,
        owner_email=owner_email,
        due_date=normalize_due_date(payload.due_date),
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
    out = _serialize_task(db, item, proj_refs, {item.session_id: s2.project_id if s2 else None})

    from services.webhook_sender import send_event_bg
    send_event_bg("task.created", {
        "task_id": item.id,
        "session_id": item.session_id,
        "project_external_id": out.get("project_external_id"),
        "owner_external_id": (out.get("owner") or {}).get("employee_external_id"),
        "origin": "manual",
    }, tenant_id=ctx.tenant.id)
    return out


# ══════════════════════════════════════════════════════════════════════
# PREGUNTAR (RAG) — mismo motor que usa Acten por dentro
# ══════════════════════════════════════════════════════════════════════

class AskV1PriorTurn(BaseModel):
    question: str
    answer: str = ""


class AskV1Request(BaseModel):
    question: str
    project_external_id: Optional[str] = None
    session_ids: Optional[list[int]] = None
    top_k: int = Field(default=8, ge=1, le=20)
    # Turnos previos del mismo hilo, para preguntas de seguimiento
    # («¿y quién lo hace?»). Se envían los últimos N en orden cronológico.
    prior_turns: Optional[list[AskV1PriorTurn]] = None


@router.post("/ask")
async def ask_v1(
    payload: AskV1Request,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("ask:query")),
):
    """Pregunta en lenguaje natural sobre las actas.

    El alcance se recorta **antes** de llamar al motor: si viene
    `X-On-Behalf-Of`, sólo se busca en las sesiones de los proyectos donde
    esa persona es miembro. Sin ese recorte alguien podría preguntar por
    un proyecto ajeno y recibir la respuesta en prosa.
    """
    from routers.ask import AskRequest, ask as _ask_core

    project_id: Optional[int] = None
    if payload.project_external_id:
        project_id = _resolve_project(
            db, ctx.tenant.id, payload.project_external_id,
        ).id
        if ctx.on_behalf_of and project_id not in ctx.visible_project_ids:
            raise HTTPException(404, "Proyecto no encontrado.")

    sids = payload.session_ids
    visible = _visible_session_ids(db, ctx)
    if visible is not None:
        if not visible:
            return {
                "answer": "No tiene reuniones visibles todavía.",
                "structured": None, "citations": [],
                "chunks_used": 0, "model": "",
            }
        sids = [s for s in sids if s in set(visible)] if sids else visible

    from routers.ask import PriorTurn as _PriorTurn
    turns = [
        _PriorTurn(question=t.question, answer=t.answer)
        for t in (payload.prior_turns or [])
    ] or None

    core = AskRequest(
        question=payload.question,
        project_id=project_id,
        top_k=payload.top_k,
        session_ids=sids,
        prior_turns=turns,
    )
    # El motor guarda la pregunta en el historial y necesita un `user.id`.
    # Si la persona existe allá pero aún no tiene cuenta aquí, atribuimos
    # la consulta a un administrador del tenant en vez de reventar.
    actor = ctx.acting_user
    if actor is None:
        from models import User as _User
        actor = db.exec(
            select(_User)
            .where(_User.tenant_id == ctx.tenant.id)
            .where(_User.is_active == True)  # noqa: E712
            .order_by(_User.id)
        ).first()
        if actor is None:
            raise HTTPException(503, "El tenant no tiene usuarios activos.")

    res = await _ask_core(payload=core, db=db, user=actor, tenant=ctx.tenant)
    # `citations` trae **una entrada por trozo leído**: la misma reunión
    # aparece tres veces si la respuesta se apoyó en su acuerdo, su riesgo
    # y su transcripción. Es lo correcto para trazar cada afirmación, pero
    # quien pinta «Sesión #N» necesita agrupar antes, y usar `session_id`
    # como clave de lista rompe el renderizador porque no es única.
    #
    # Se añade la vista agrupada sin quitar la detallada.
    datos = res.model_dump() if hasattr(res, "model_dump") else dict(res)
    vistas: dict[int, dict[str, Any]] = {}
    for c in datos.get("citations") or []:
        sid = c.get("session_id")
        if sid is None:
            continue
        entrada = vistas.setdefault(sid, {
            "session_id": sid,
            "session_title": c.get("session_title"),
            "session_date": c.get("session_date"),
            "project_name": c.get("project_name"),
            "fragmentos": 0,
            # La distancia menor de todas: es el trozo que mejor encajó.
            "mejor_distancia": c.get("distance"),
        })
        entrada["fragmentos"] += 1
        d = c.get("distance")
        if d is not None and (
            entrada["mejor_distancia"] is None or d < entrada["mejor_distancia"]
        ):
            entrada["mejor_distancia"] = d
        entrada["session_title"] = entrada["session_title"] or c.get("session_title")
        entrada["session_date"] = entrada["session_date"] or c.get("session_date")
    datos["cited_sessions"] = sorted(
        vistas.values(),
        key=lambda x: (x["mejor_distancia"] if x["mejor_distancia"] is not None else 9),
    )
    return datos


# ══════════════════════════════════════════════════════════════════════
# CALENDARIO
# ══════════════════════════════════════════════════════════════════════

@router.get("/calendar/events")
def list_calendar_events(
    project_external_id: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    include: str = Query(
        "tasks,sessions,events",
        description="Qué pintar: coma entre 'tasks', 'sessions' y 'events'.",
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=MAX_LIMIT),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("calendar:read")),
):
    """El calendario tal como se ve en Acten.

    **No son solo los eventos de Google/Microsoft.** El calendario de Acten
    se arma sobre todo con **tareas que tienen fecha de vencimiento** y con
    las **reuniones**; las agendas OAuth son el tercer ingrediente y hoy
    están vacías porque nadie ha conectado una. Devolver solo esas dejaba
    el calendario en blanco pese a que en pantalla hay datos.

    Cada elemento trae `kind`: `task` | `session` | `event`.

    `CalendarEvent` no lleva `tenant_id`: cuelga de la cuenta OAuth de un
    usuario. El aislamiento se hace por el join con `user.tenant_id`; no
    se puede filtrar sólo por el evento.
    """
    from models import CalendarAccount, CalendarEvent, User

    quiere = {x.strip() for x in (include or "").split(",") if x.strip()}
    proj_refs = _project_ref_map(db, ctx.tenant.id)
    vis = ctx.visible_project_ids or []
    items: list[dict[str, Any]] = []

    # ── Tareas con fecha ───────────────────────────────────────────────
    # Es lo que realmente llena el calendario de Acten. En Acten un
    # administrador ve las de todo el tenant; aquí se recorta por persona,
    # que es lo acordado para la integración.
    if "tasks" in quiere:
        tq = (
            select(ActionItem, MeetingSession.project_id)
            .join(MeetingSession, MeetingSession.id == ActionItem.session_id)
            .where(ActionItem.tenant_id == ctx.tenant.id)
            .where(ActionItem.due_date.is_not(None))
            .where(ActionItem.due_date != "")
        )
        if ctx.on_behalf_of:
            if not vis:
                tq = tq.where(MeetingSession.project_id == -1)
            else:
                tq = tq.where(MeetingSession.project_id.in_(vis))
        if date_from:
            tq = tq.where(ActionItem.due_date >= date_from[:10])
        if date_to:
            tq = tq.where(ActionItem.due_date <= date_to[:10])
        for t, pid in db.exec(tq).all():
            if project_external_id and proj_refs.get(pid) != project_external_id:
                continue
            # `due_date` es texto libre y arrastró basura histórica del
            # extractor: hubo tareas con «No especificada» ahí. Colarlas
            # como `start_at` rompería cualquier calendario del otro lado,
            # así que solo pasan las que son una fecha de verdad.
            fecha = normalize_due_date(t.due_date)
            if not fecha:
                continue
            inicio = fecha + ("T" + t.due_time if t.due_time else "")
            items.append({
                "kind": "task",
                "id": t.id,
                "title": t.title or "",
                "start_at": inicio,
                "end_at": inicio,
                "all_day": not t.due_time,
                "status": t.status or "pending",
                "priority": t.priority or "media",
                "owner": duenio,
                "unassigned": duenio is None,
                "project_external_id": proj_refs.get(pid) if pid else None,
                "session_id": t.session_id,
                "meeting_url": None,
                "attendees": [],
            })

    # ── Reuniones ──────────────────────────────────────────────────────
    if "sessions" in quiere:
        sq = (
            select(MeetingSession)
            .where(MeetingSession.tenant_id == ctx.tenant.id)
            .where(MeetingSession.status != "archived")
        )
        if ctx.on_behalf_of:
            if not vis:
                sq = sq.where(MeetingSession.project_id == -1)
            else:
                sq = sq.where(MeetingSession.project_id.in_(vis))
        if date_from:
            sq = sq.where(MeetingSession.date >= date_from)
        if date_to:
            sq = sq.where(MeetingSession.date <= date_to)
        for ms in db.exec(sq).all():
            if project_external_id and proj_refs.get(ms.project_id) != project_external_id:
                continue
            items.append({
                "kind": "session",
                "id": ms.id,
                "title": ms.title or "",
                "start_at": ms.date,
                "end_at": ms.date,
                "all_day": False,
                "status": ms.status,
                "project_external_id": (
                    proj_refs.get(ms.project_id) if ms.project_id else None
                ),
                "session_id": ms.id,
                "meeting_url": None,
                "attendees": [],
            })

    if "events" not in quiere:
        items.sort(key=lambda x: x["start_at"] or "", reverse=True)
        return _paginar(items, page, limit)

    q = (
        select(CalendarEvent, CalendarAccount.user_id)
        .join(CalendarAccount, CalendarAccount.id == CalendarEvent.calendar_account_id)
        .join(User, User.id == CalendarAccount.user_id)
        .where(User.tenant_id == ctx.tenant.id)
    )

    if ctx.on_behalf_of:
        mine = ctx.acting_user.id if ctx.acting_user else -1
        vis = ctx.visible_project_ids or []
        # Ve su propia agenda y la de los proyectos donde es miembro.
        if vis:
            q = q.where(
                (CalendarAccount.user_id == mine)
                | (CalendarEvent.project_id.in_(vis))
            )
        else:
            q = q.where(CalendarAccount.user_id == mine)

    if project_external_id:
        proj = _resolve_project(db, ctx.tenant.id, project_external_id)
        if ctx.on_behalf_of and proj.id not in (ctx.visible_project_ids or []):
            raise HTTPException(404, "Proyecto no encontrado.")
        q = q.where(CalendarEvent.project_id == proj.id)
    if date_from:
        q = q.where(CalendarEvent.start_at >= date_from)
    if date_to:
        q = q.where(CalendarEvent.start_at <= date_to)

    import json as _json
    for row in db.exec(q).all():
        ev = row[0] if isinstance(row, tuple) else row
        try:
            attendees = _json.loads(ev.attendees_json or "[]")
        except (ValueError, TypeError):
            attendees = []
        items.append({
            "kind": "event",
            "id": ev.id,
            "title": ev.title or "",
            "start_at": ev.start_at,
            "end_at": ev.end_at,
            "all_day": False,
            "status": None,
            "project_external_id": (
                proj_refs.get(ev.project_id) if ev.project_id else None
            ),
            "session_id": ev.session_id,
            "meeting_url": ev.meeting_url,
            "attendees": attendees,
        })

    items.sort(key=lambda x: x["start_at"] or "", reverse=True)
    return _paginar(items, page, limit)


# ══════════════════════════════════════════════════════════════════════
# ACTA EN DOCUMENTO (docx / pdf)
# ══════════════════════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/document")
def get_session_document(
    session_id: int,
    format: str = Query("pdf", pattern="^(pdf|docx)$"),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Acta de la reunión con la marca del tenant, en PDF o Word."""
    from fastapi.responses import Response

    s = db.get(MeetingSession, session_id)
    if not s or s.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Sesión no encontrada.")
    if ctx.on_behalf_of and s.project_id not in (ctx.visible_project_ids or []):
        raise HTTPException(404, "Sesión no encontrada.")

    from routers.sessions_upload import generate_word_document_bytes

    items = db.exec(
        select(ActionItem).where(ActionItem.session_id == s.id)
    ).all()
    docx_bytes = generate_word_document_bytes(s, items, db).getvalue()
    safe = "".join(
        c for c in (s.title or f"acta-{s.id}") if c.isalnum() or c in " -_"
    ).strip() or f"acta-{s.id}"

    if format == "docx":
        return Response(
            content=docx_bytes,
            media_type=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            headers={
                "Content-Disposition": f'attachment; filename="{safe}.docx"'
            },
        )

    # PDF: Gotenberg convierte el docx (conserva la plantilla corporativa).
    import requests
    from config import settings

    base = (settings.gotenberg_url or "http://gotenberg:3000").rstrip("/")
    try:
        r = requests.post(
            f"{base}/forms/libreoffice/convert",
            files={"files": ("acta.docx", docx_bytes)},
            timeout=60,
        )
        if not r.ok:
            raise RuntimeError(f"Gotenberg {r.status_code}: {r.text[:200]}")
        pdf_bytes = r.content
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gotenberg falló para sesión %s: %s", s.id, exc)
        from services.pdf_generator import CorporatePDFGenerator
        from routers.sessions_upload import __build_corporate_data as _bcd
        pdf_bytes = CorporatePDFGenerator(_bcd(s, items, db)).generar_buffer().getvalue()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'},
    )
