"""Búsqueda global del topbar.

Endpoint único `GET /api/search?q=...` que devuelve coincidencias
agrupadas por tipo: meetings, projects, tasks, people. Multi-tenant
estricto (todo filtrado por `tenant_id`).

Diseño:
- Búsqueda LIKE case-insensitive sobre los campos visibles principales.
- Hard cap: 5 hits por categoría, total <= 25.
- Cada hit trae:
    - type: 'meeting' | 'project' | 'task' | 'person'
    - id, title, subtitle, link_to (deep link interno)
- No usa el embedding RAG (eso vive en /api/ask). Esto es un quick-jump
  literal — pensado para "encontrar X y abrirlo en 1 click".
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, or_, select

from database import get_session
from models import (
    ActionItem,
    MeetingSession,
    Project,
    ProjectContact,
    Tenant,
    User,
)
from routers.auth import get_current_tenant, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/search", tags=["Search (Global)"])

# Tope total / por categoría — el dropdown no debería listar más; el
# usuario afina la query si quiere precisión.
PER_CATEGORY_LIMIT = 5
HARD_LIMIT = 50


def _like(term: str) -> str:
    """Envuelve el término en %...% para LIKE. Escapa wildcards básicos."""
    safe = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{safe}%"


@router.get("")
@router.get("/")
def global_search(
    q: str = Query(..., min_length=2, max_length=120, description="Texto a buscar"),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Búsqueda global multi-categoría. Devuelve resultados listos para
    pintarse en el dropdown del topbar."""
    term = q.strip()
    if len(term) < 2:
        return {"query": q, "groups": [], "total": 0}

    pat = _like(term)

    # ------------------------------------------------------------ Sessions
    sess_rows = db.exec(
        select(MeetingSession)
        .where(MeetingSession.tenant_id == tenant.id)
        .where(
            or_(
                MeetingSession.title.ilike(pat),
                MeetingSession.raw_summary.ilike(pat),
                MeetingSession.processed_decisions.ilike(pat),
                MeetingSession.processed_attendees.ilike(pat),
            )
        )
        .order_by(MeetingSession.id.desc())
        .limit(PER_CATEGORY_LIMIT)
    ).all()
    meetings = [
        {
            "type": "meeting",
            "id": s.id,
            "title": s.title or f"Sesión #{s.id}",
            "subtitle": _trim((s.raw_summary or "").strip(), 90),
            "link_to": f"/admin/curation/{s.id}",
            "meta": {"status": s.status, "date": s.date},
        }
        for s in sess_rows
    ]

    # ------------------------------------------------------------ Projects
    proj_rows = db.exec(
        select(Project)
        .where(Project.tenant_id == tenant.id)
        .where(
            or_(
                Project.name.ilike(pat),
                Project.description.ilike(pat),
            )
        )
        .order_by(Project.id.desc())
        .limit(PER_CATEGORY_LIMIT)
    ).all()
    projects = [
        {
            "type": "project",
            "id": p.id,
            "title": p.name,
            "subtitle": _trim((p.description or "Sin descripción"), 90),
            "link_to": f"/admin/projects/{p.id}",
            "meta": {"is_active": p.is_active},
        }
        for p in proj_rows
    ]

    # ------------------------------------------------------------ Action items
    task_rows = db.exec(
        select(ActionItem)
        .where(ActionItem.tenant_id == tenant.id)
        .where(
            or_(
                ActionItem.title.ilike(pat),
                ActionItem.description.ilike(pat),
                ActionItem.owner_name.ilike(pat),
                ActionItem.owner_email.ilike(pat),
            )
        )
        .order_by(ActionItem.id.desc())
        .limit(PER_CATEGORY_LIMIT)
    ).all()
    tasks = [
        {
            "type": "task",
            "id": t.id,
            "title": t.title or "Tarea sin título",
            "subtitle": (
                f"{t.owner_name or 'Sin asignar'}"
                + (f" · vence {t.due_date}" if t.due_date else "")
            ),
            "link_to": f"/admin/curation/{t.session_id}",
            "meta": {"status": t.status, "session_id": t.session_id},
        }
        for t in task_rows
    ]

    # ------------------------------------------------------------ People
    # Mezcla: usuarios del workspace + contactos de proyectos.
    user_rows = db.exec(
        select(User)
        .where(User.tenant_id == tenant.id)
        .where(
            or_(
                User.full_name.ilike(pat),
                User.email.ilike(pat),
            )
        )
        .limit(PER_CATEGORY_LIMIT)
    ).all()
    # ProjectContact no tiene `tenant_id` directo — el aislamiento viene
    # vía join con Project (que sí lo tiene). Hacemos JOIN explícito.
    contact_rows = db.exec(
        select(ProjectContact)
        .join(Project, ProjectContact.project_id == Project.id)
        .where(Project.tenant_id == tenant.id)
        .where(
            or_(
                ProjectContact.name.ilike(pat),
                ProjectContact.email.ilike(pat),
                ProjectContact.role.ilike(pat),
            )
        )
        .limit(PER_CATEGORY_LIMIT)
    ).all()

    people: list[dict] = []
    for u in user_rows:
        people.append({
            "type": "person",
            "id": u.id,
            "title": u.full_name or u.email,
            "subtitle": f"{u.email} · usuario del workspace",
            # No tenemos pantalla pública de perfil; deep-link a su panel
            # de tareas filtradas (cuando exista). Por ahora va a /pendientes.
            "link_to": "/admin/pendientes",
            "meta": {"role": getattr(u, "role", None) or ""},
        })
    for c in contact_rows:
        people.append({
            "type": "person",
            "id": c.id,
            "title": c.name,
            "subtitle": f"{c.email or 'sin email'}"
                        + (f" · {c.role}" if c.role else "")
                        + (f" · {c.entity}" if c.entity else ""),
            "link_to": f"/admin/projects/{c.project_id}",
            "meta": {"contact": True, "project_id": c.project_id},
        })
    people = people[:PER_CATEGORY_LIMIT]

    # ------------------------------------------------------------ Compose
    groups = [
        {"label": "Reuniones", "items": meetings},
        {"label": "Proyectos", "items": projects},
        {"label": "Tareas",    "items": tasks},
        {"label": "Personas",  "items": people},
    ]
    # Filtra grupos vacíos para no mostrarlos en el dropdown.
    groups = [g for g in groups if g["items"]]
    total = sum(len(g["items"]) for g in groups)

    return {"query": q, "groups": groups, "total": total}


def _trim(text: Optional[str], n: int) -> str:
    if not text:
        return ""
    text = text.strip().replace("\n", " ")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"
