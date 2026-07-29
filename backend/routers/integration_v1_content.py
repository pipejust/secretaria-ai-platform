"""API pública v1 — lo que Acten produce a partir de una sesión.

Complementa a `integration_v1.py` (sesiones y tareas) y a
`integration_v1_platform.py` (conectar plataformas). Aquí va el resto de
lo que hace Acten y que su interfaz necesita poder ofrecer:

* enviar el acta por correo con su marca
* generar artefactos por rol (PRD, brief comercial, informe de estado…)
* comentarios y versiones de una sesión
* búsqueda global y analítica

Todo respeta `X-On-Behalf-Of`: una persona solo alcanza lo de los
proyectos donde es miembro.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database import get_session
from models import (
    ActionItem,
    Comment,
    MeetingSession,
    MeetingSessionVersion,
    OutputTemplate,
    Project,
    SessionOutput,
    User,
)
from services.api_key_auth import IntegrationContext, require_scopes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Integración v1 — contenido"])


def _sesion(db: Session, ctx: IntegrationContext, session_id: int) -> MeetingSession:
    s = db.get(MeetingSession, session_id)
    if not s or s.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Sesión no encontrada.")
    if ctx.on_behalf_of and s.project_id not in (ctx.visible_project_ids or []):
        raise HTTPException(404, "Sesión no encontrada.")
    return s


def _actor(ctx: IntegrationContext) -> User:
    """Persona en cuyo nombre se actúa. Distingue «falta la cabecera» de
    «esa persona no está enlazada»: son dos arreglos distintos."""
    if ctx.acting_user:
        return ctx.acting_user
    if not ctx.on_behalf_of:
        raise HTTPException(
            400, "Falta la cabecera X-On-Behalf-Of (UUID del empleado).",
        )
    raise HTTPException(
        422,
        "Esa persona aún no tiene cuenta en Acten. Enlázala primero con el "
        "asistente (`/api/v1/link`).",
    )


# ══════════════════════════════════════════════════════════════════════
# ENVIAR EL ACTA POR CORREO
# ══════════════════════════════════════════════════════════════════════

class EnviarActaIn(BaseModel):
    action_item_ids: list[int] = Field(
        default_factory=list,
        description="Tareas a incluir. Vacío = todas las de la sesión.",
    )
    adjuntar_documento: bool = True


@router.post("/sessions/{session_id}/email")
async def enviar_acta(
    session_id: int,
    payload: EnviarActaIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:send")),
):
    """Envía el acta por correo a los responsables, con la marca del tenant.

    ⚠️ **Es el único endpoint de la v1 que manda correo a terceros.** Por
    eso pide su propio alcance `sessions:send`, que no va incluido en
    `sessions:read`: quien pueda leer un acta no debería poder, por
    descuido, mandársela a media empresa.

    Los destinatarios salen de los responsables de las tareas, no del
    cuerpo de la petición — así una llamada no puede dirigir el acta a
    una dirección arbitraria.
    """
    from routers.sessions_upload import DispatchEmailsRequest, dispatch_emails

    s = _sesion(db, ctx, session_id)
    ids = payload.action_item_ids or [
        r.id for r in db.exec(
            select(ActionItem).where(ActionItem.session_id == s.id)
        ).all()
    ]
    if not ids:
        raise HTTPException(422, "La sesión no tiene tareas que notificar.")

    return await dispatch_emails(
        session_id=s.id,
        request=DispatchEmailsRequest(
            action_item_ids=ids, attach_document=payload.adjuntar_documento,
        ),
        db=db, tenant=ctx.tenant, _writer=_actor(ctx),
    )


# ══════════════════════════════════════════════════════════════════════
# DESPACHO A LAS PLATAFORMAS CONECTADAS
# ══════════════════════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/dispatch")
async def despachar_a_plataformas(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:write")),
):
    """Manda las tareas de la sesión a las plataformas del proyecto.

    Es el botón «enviar a Jira / Trello / Slack» de su interfaz. Acten ya
    lo hace solo cuando llega una sesión; esto es para relanzarlo cuando
    alguien corrigió las tareas después.
    """
    from routers.sessions_upload import DispatchPlatformsRequest, dispatch_platforms

    s = _sesion(db, ctx, session_id)
    ids = [
        r.id for r in db.exec(
            select(ActionItem).where(ActionItem.session_id == s.id)
        ).all()
    ]
    return await dispatch_platforms(
        session_id=s.id,
        request=DispatchPlatformsRequest(action_item_ids=ids),
        db=db, tenant=ctx.tenant, _writer=_actor(ctx),
    )


# ══════════════════════════════════════════════════════════════════════
# ARTEFACTOS POR ROL (PRD, brief comercial, informe de estado…)
# ══════════════════════════════════════════════════════════════════════

@router.get("/output-templates")
def listar_plantillas_salida(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    """Qué artefactos sabe generar Acten para este tenant."""
    rows = db.exec(
        select(OutputTemplate)
        .where(OutputTemplate.tenant_id == ctx.tenant.id)
        .where(OutputTemplate.is_active == True)  # noqa: E712
    ).all()
    return {
        "items": [
            {
                "id": t.id, "nombre": t.name, "rol": t.role_type,
                "formato": t.output_format,
            }
            for t in rows
        ],
        "total": len(rows),
    }


@router.get("/sessions/{session_id}/outputs")
def listar_salidas(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:read")),
):
    s = _sesion(db, ctx, session_id)
    rows = db.exec(
        select(SessionOutput).where(SessionOutput.session_id == s.id)
    ).all()
    return {
        "items": [
            {
                "id": o.id, "titulo": o.title, "cuerpo": o.body,
                "formato": o.output_format, "template_id": o.template_id,
                "creado_el": o.created_at,
            }
            for o in rows
        ],
        "total": len(rows),
    }


@router.post("/sessions/{session_id}/outputs", status_code=201)
async def generar_salida(
    session_id: int,
    template_id: int = Query(...),
    provider: str = Query("openai", pattern="^(openai|groq)$"),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("outputs:write")),
):
    """Genera un artefacto a partir de la transcripción (llama a un modelo)."""
    from routers.role_outputs import generate_output

    s = _sesion(db, ctx, session_id)
    tpl = db.get(OutputTemplate, template_id)
    if not tpl or tpl.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Plantilla no encontrada.")
    return await generate_output(
        session_id=s.id, template_id=template_id, provider=provider,
        db=db, current_user=_actor(ctx),
    )


# ══════════════════════════════════════════════════════════════════════
# COMENTARIOS Y VERSIONES
# ══════════════════════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/comments")
def listar_comentarios(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("comments:read")),
):
    s = _sesion(db, ctx, session_id)
    rows = db.exec(
        select(Comment).where(Comment.session_id == s.id).order_by(Comment.id)
    ).all()
    autores = {
        u.id: u.full_name or u.email
        for u in db.exec(select(User).where(User.tenant_id == ctx.tenant.id)).all()
    }
    return {
        "items": [
            {
                "id": c.id, "seccion": c.section, "ref_id": c.ref_id,
                "cuerpo": c.body, "autor": autores.get(c.author_user_id, ""),
                "padre_id": c.parent_comment_id,
                "resuelto_el": c.resolved_at, "creado_el": c.created_at,
            }
            for c in rows
        ],
        "total": len(rows),
    }


class ComentarioIn(BaseModel):
    cuerpo: str
    seccion: Optional[str] = None
    ref_id: Optional[str] = None
    padre_id: Optional[int] = None


@router.post("/sessions/{session_id}/comments", status_code=201)
def crear_comentario(
    session_id: int,
    payload: ComentarioIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("comments:write")),
):
    s = _sesion(db, ctx, session_id)
    cuerpo = (payload.cuerpo or "").strip()
    if not cuerpo:
        raise HTTPException(422, "El comentario está vacío.")
    c = Comment(
        session_id=s.id, section=payload.seccion, ref_id=payload.ref_id,
        author_user_id=_actor(ctx).id, body=cuerpo,
        parent_comment_id=payload.padre_id,
    )
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "creado_el": c.created_at}


@router.get("/sessions/{session_id}/versions")
def listar_versiones(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Historial de ediciones del acta. El contenido va en `snapshot`."""
    s = _sesion(db, ctx, session_id)
    rows = db.exec(
        select(MeetingSessionVersion)
        .where(MeetingSessionVersion.session_id == s.id)
        .order_by(MeetingSessionVersion.version_number.desc())
    ).all()
    out = []
    for v in rows:
        try:
            snap = json.loads(v.snapshot_json or "{}")
        except (json.JSONDecodeError, TypeError):
            snap = {}
        out.append({
            "id": v.id, "version": v.version_number,
            "editada_por_user_id": v.edited_by_user_id,
            "creada_el": v.created_at, "snapshot": snap,
        })
    return {"items": out, "total": len(out)}


# ══════════════════════════════════════════════════════════════════════
# BÚSQUEDA Y ANALÍTICA
# ══════════════════════════════════════════════════════════════════════

@router.get("/search")
def buscar(
    q: str = Query(..., min_length=2, max_length=120),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Búsqueda global (reuniones, tareas, proyectos, personas).

    Es búsqueda literal, distinta del `/ask`: sirve para el buscador del
    encabezado, no para preguntar en lenguaje natural.
    """
    from routers.search import global_search

    res = global_search(q=q, db=db, user=_actor(ctx), tenant=ctx.tenant)

    if not ctx.on_behalf_of:
        return res
    # El buscador interno no conoce `X-On-Behalf-Of`. Se recorta aquí: sin
    # esto, escribir tres letras devolvería títulos de proyectos ajenos.
    vis = set(ctx.visible_project_ids or [])
    ids_ok = {
        r[0] if isinstance(r, tuple) else r
        for r in db.exec(
            select(MeetingSession.id)
            .where(MeetingSession.tenant_id == ctx.tenant.id)
            .where(MeetingSession.project_id.in_(vis or [-1]))
        ).all()
    }
    refs_ok = {
        p.external_ref for p in db.exec(
            select(Project).where(Project.id.in_(vis or [-1]))
        ).all()
    }
    nombres_ok = {
        p.name for p in db.exec(
            select(Project).where(Project.id.in_(vis or [-1]))
        ).all()
    }

    def permitido(item: dict, tipo: str) -> bool:
        if tipo in ("sessions", "sesiones"):
            return item.get("id") in ids_ok
        if tipo in ("projects", "proyectos"):
            return item.get("name") in nombres_ok or item.get("id") in vis
        if tipo in ("tasks", "tareas", "pendientes"):
            return item.get("session_id") in ids_ok
        return False  # personas y demás: no se filtran bien, se ocultan

    grupos = []
    total = 0
    for g in (res.get("groups") or []):
        tipo = (g.get("type") or g.get("tipo") or "").lower()
        items = [i for i in (g.get("items") or []) if permitido(i, tipo)]
        if items:
            grupos.append({**g, "items": items})
            total += len(items)
    return {"query": res.get("query", q), "groups": grupos, "total": total}


@router.get("/analytics/roi")
def analitica_roi(
    days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("analytics:read")),
):
    """Horas de reunión, tareas generadas y cumplimiento del periodo.

    Es agregado de **todo el tenant**, no de una persona — por eso pide su
    propio alcance y no se recorta con `X-On-Behalf-Of`. No lo expongan a
    cualquier empleado sin pensarlo.
    """
    from routers.analytics import roi_dashboard

    return roi_dashboard(days=days, db=db, current_user=_actor(ctx))


@router.get("/analytics/recurring")
def analitica_recurrentes(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("analytics:read")),
):
    """Temas que se repiten sesión tras sesión sin cerrarse."""
    from routers.analytics import recurring_meetings

    return recurring_meetings(db=db, current_user=_actor(ctx))


@router.get("/sessions/{session_id}/quality")
def calidad_sesion(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Puntuación de calidad del acta (claridad, decisiones, responsables)."""
    from routers.analytics import session_quality

    s = _sesion(db, ctx, session_id)
    return session_quality(session_id=s.id, db=db, current_user=_actor(ctx))


# ══════════════════════════════════════════════════════════════════════
# NOTIFICACIONES
# ══════════════════════════════════════════════════════════════════════

@router.get("/notifications")
def notificaciones(
    solo_no_leidas: bool = False,
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("notifications:read")),
):
    """Avisos dirigidos a esa persona (acta lista, tarea asignada, mención)."""
    from models import Notification

    user = _actor(ctx)
    q = (
        select(Notification)
        .where(Notification.tenant_id == ctx.tenant.id)
        .where(Notification.user_id == user.id)
    )
    if solo_no_leidas:
        q = q.where(Notification.is_read == False)  # noqa: E712
    rows = db.exec(q.order_by(Notification.id.desc()).limit(limit)).all()
    return {
        "items": [
            {
                "id": n.id, "tipo": n.kind, "titulo": n.title, "cuerpo": n.body,
                "entidad": n.entity_type, "entidad_id": n.entity_id,
                "leida": n.is_read, "creada_el": n.created_at,
            }
            for n in rows
        ],
        "total": len(rows),
    }
