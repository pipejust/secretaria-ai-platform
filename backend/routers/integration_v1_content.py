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
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from database import get_session
from models import (
    Project,
    ActionItem,
    Comment,
    MeetingSession,
    MeetingSessionVersion,
    OutputTemplate,
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


def _actor_o_sistema(ctx: IntegrationContext, db: Session) -> User:
    """Un `User` para operaciones de **lectura** que necesitan uno.

    En modo empresa (`X-On-Behalf-Of: *`) no hay persona detrás, pero la
    búsqueda y la analítica piden un usuario solo para resolver el tenant.
    Se usa cualquier cuenta activa: no cambia lo que se devuelve, porque
    en ese modo no se recorta por persona.

    Las escrituras siguen usando `_actor`: un comentario o un correo
    necesitan un autor de verdad, y «la empresa» no firma nada.
    """
    if ctx.acting_user:
        return ctx.acting_user
    if ctx.org_wide:
        u = db.exec(
            select(User)
            .where(User.tenant_id == ctx.tenant.id)
            .where(User.is_active == True)  # noqa: E712
            .order_by(User.id)
        ).first()
        if u:
            return u
        raise HTTPException(503, "La empresa no tiene usuarios activos.")
    return _actor(ctx)


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
# CURACIÓN — corregir lo que sacó la IA
# ══════════════════════════════════════════════════════════════════════

class ParticipanteIn(BaseModel):
    """Un asistente. Solo hace falta el nombre."""

    name: str = Field(min_length=1)
    role: Optional[str] = None
    entity: Optional[str] = None
    email: Optional[str] = None


class ActaPatch(BaseModel):
    """Campos del acta. Solo se toca lo que venga; el resto no se roza.

    `decisions`, `agreements` y `risks` son **texto libre**: se guardan
    tal cual llegan. Acten los emite en markdown —típicamente una lista
    con viñetas— pero no impone forma al escribir, así que lo que manden
    es exactamente lo que se devolverá después.
    """

    title: Optional[str] = None
    summary: Optional[str] = None
    decisions: Optional[str] = None
    agreements: Optional[str] = None
    risks: Optional[str] = None
    language: Optional[str] = None
    status: Optional[str] = None
    project_external_id: Optional[str] = None
    date: Optional[str] = None
    # **Reemplaza** la lista entera, no añade. Es lo que hace falta para
    # una pantalla de curación: quien corrige quita a quien no estuvo, y
    # con una semántica de «añadir» eso sería imposible.
    participants: Optional[list[ParticipanteIn]] = None


def _fecha_valida(v: str) -> str:
    """Devuelve la fecha normalizada o lanza `422`.

    Se acepta `YYYY-MM-DD` y la forma completa con hora. Las sesiones
    guardan el instante con zona (`2026-08-04T14:49:36+00:00`), así que
    una fecha suelta se completa a medianoche en vez de rechazarla: quien
    corrige un acta escribe el día, no el segundo.
    """
    from datetime import datetime as _dt

    texto = (v or "").strip()
    if not texto:
        raise HTTPException(422, "La fecha viene vacía.")
    try:
        if len(texto) == 10:
            _dt.strptime(texto, "%Y-%m-%d")
            return f"{texto}T00:00:00+00:00"
        _dt.fromisoformat(texto.replace("Z", "+00:00"))
        return texto
    except ValueError:
        raise HTTPException(
            422,
            f"Fecha no reconocida: {texto!r}. Usa YYYY-MM-DD o ISO-8601 "
            f"completo (2026-08-04T14:49:36+00:00).",
        )


@router.patch("/sessions/{session_id}")
def editar_acta(
    session_id: int,
    payload: ActaPatch,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:write")),
):
    """Guarda la corrección de una persona sobre el acta.

    **Se guarda lo que se manda, sin volver a pasar por el modelo.** Si
    guardar volviera a generar el texto, la corrección se perdería sin que
    nadie se entere — y quien corrige un acta lo hace justamente porque el
    modelo se equivocó. Para pedir sugerencias está `suggest-fields`, que
    es una acción aparte y explícita.
    """
    s = _sesion(db, ctx, session_id)

    if payload.project_external_id is not None:
        ref = payload.project_external_id.strip()
        if not ref:
            s.project_id = None
        else:
            proj = db.exec(
                select(Project)
                .where(Project.tenant_id == ctx.tenant.id)
                .where(Project.external_ref == ref)
            ).first()
            if not proj:
                raise HTTPException(
                    422, f"No existe un proyecto sincronizado con id '{ref}'.",
                )
            s.project_id = proj.id

    if payload.date is not None:
        s.date = _fecha_valida(payload.date)

    if payload.participants is not None:
        # Se pasan por el registro de alias: si llega «JDiego Toro» se
        # guarda «Juan Diego Toro», que es como aparece en el resto de la
        # plataforma. Y se funden los que resulten iguales.
        from services.alias_personas import cargar, canonizar, normalizar

        try:
            tabla = cargar(db, ctx.tenant.id)
        except Exception:  # noqa: BLE001
            tabla = {}
        limpios: list[dict[str, str]] = []
        vistos: set[str] = set()
        for p in payload.participants:
            nombre, correo = canonizar(tabla, p.name, p.email)
            nombre = (nombre or "").strip()
            if not nombre:
                continue
            clave = normalizar(nombre)
            if clave in vistos:
                continue
            vistos.add(clave)
            limpios.append({
                "name": nombre,
                "role": (p.role or "").strip() or "—",
                "entity": (p.entity or "").strip() or "—",
                "email": (correo or "").strip(),
            })
        s.processed_attendees = json.dumps(limpios, ensure_ascii=False)

    campos = {
        "title": "title",
        "summary": "raw_summary",
        "decisions": "processed_decisions",
        "agreements": "processed_agreements",
        "risks": "processed_risks",
        "language": "language",
        "status": "status",
    }
    tocados = []
    for entrada, columna in campos.items():
        valor = getattr(payload, entrada)
        if valor is not None:
            setattr(s, columna, valor)
            tocados.append(entrada)
    if payload.project_external_id is not None:
        tocados.append("project_external_id")
    if payload.date is not None:
        tocados.append("date")
    if payload.participants is not None:
        tocados.append("participants")

    if not tocados:
        raise HTTPException(422, "No se envió ningún campo que cambiar.")

    db.add(s)
    db.commit()
    db.refresh(s)

    try:
        asistentes = json.loads(s.processed_attendees or "[]")
    except (ValueError, TypeError):
        asistentes = []
    return {
        "id": s.id,
        "actualizados": tocados,
        "date": s.date,
        "participants": asistentes,
    }


@router.post("/sessions/{session_id}/regenerate-tasks")
async def regenerar_tareas(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("tasks:write")),
):
    """Vuelve a extraer las tareas de la transcripción con IA.

    **Se puede una sola vez por sesión**, igual que en Acten: la segunda
    llamada responde `409`. El límite existe porque regenerar borra las
    tareas actuales, y quien ya corrigió una a mano no debería perderla
    por pulsar dos veces.
    """
    from routers.sessions_upload import (
        RegeneratePayload, regenerate_tasks_from_transcript,
    )

    s = _sesion(db, ctx, session_id)
    # Se acepta también en modo empresa: regenerar es una operación, no
    # una firma. Exigir una persona obligaría a la pantalla de
    # administración a elegir a alguien al azar para pulsar un botón.
    return await regenerate_tasks_from_transcript(
        session_id=s.id, payload=RegeneratePayload(),
        db=db, tenant=ctx.tenant, _writer=_actor_o_sistema(ctx, db),
    )


@router.post("/sessions/{session_id}/suggest-fields")
async def sugerir_campos(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:write")),
):
    """Sugiere idioma, decisiones, riesgos, acuerdos, asistentes y temas.

    **No toca el resumen ejecutivo**, que viene de la grabación y se edita
    a mano. Y también es de un solo uso por sesión.
    """
    from routers.sessions_upload import (
        RegeneratePayload, regenerate_fields_from_transcript,
    )

    s = _sesion(db, ctx, session_id)
    return await regenerate_fields_from_transcript(
        session_id=s.id, payload=RegeneratePayload(),
        db=db, tenant=ctx.tenant, _writer=_actor_o_sistema(ctx, db),
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

    res = global_search(q=q, db=db, user=_actor_o_sistema(ctx, db), tenant=ctx.tenant)

    if not ctx.on_behalf_of:
        return res

    # El buscador interno no conoce `X-On-Behalf-Of`. Se recorta aquí: sin
    # esto, escribir tres letras devolvería títulos de proyectos ajenos.
    # El tipo va en cada elemento (`type`), no en el grupo — el grupo solo
    # trae `label`.
    vis = set(ctx.visible_project_ids or [])
    ids_sesiones = {
        r[0] if isinstance(r, tuple) else r
        for r in db.exec(
            select(MeetingSession.id)
            .where(MeetingSession.tenant_id == ctx.tenant.id)
            .where(MeetingSession.project_id.in_(vis or {-1}))
        ).all()
    }

    def permitido(item: dict) -> bool:
        tipo = item.get("type")
        meta = item.get("meta") or {}
        if tipo == "meeting":
            return item.get("id") in ids_sesiones
        if tipo == "project":
            return item.get("id") in vis
        if tipo == "task":
            return meta.get("session_id") in ids_sesiones
        # Personas: se dejan pasar. Son compañeros del mismo tenant y el
        # resultado no revela contenido de ninguna reunión.
        return tipo == "person"

    grupos = []
    total = 0
    for g in (res.get("groups") or []):
        items = [i for i in (g.get("items") or []) if permitido(i)]
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

    return roi_dashboard(days=days, db=db, current_user=_actor_o_sistema(ctx, db))


@router.get("/analytics/recurring")
def analitica_recurrentes(
    similitud: float = Query(0.8, ge=0.5, le=1.0),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("analytics:read")),
):
    """Reuniones que se repiten, detectadas por parecido de títulos.

    `similitud` va explícito a propósito: el handler interno lo declara
    como `Query(0.8)`, y llamarlo sin él le pasa el objeto `Query` en vez
    del número. Reventaba con `'>=' not supported between float and
    Query` — un `500` que solo aparece si se ejercita, no leyendo.
    """
    from routers.analytics import recurring_meetings

    return recurring_meetings(
        similarity_threshold=similitud,
        db=db, current_user=_actor_o_sistema(ctx, db),
    )


@router.get("/sessions/{session_id}/quality")
def calidad_sesion(
    session_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("sessions:read")),
):
    """Puntuación de calidad del acta (claridad, decisiones, responsables)."""
    from routers.analytics import session_quality

    s = _sesion(db, ctx, session_id)
    return session_quality(session_id=s.id, db=db, current_user=_actor_o_sistema(ctx, db))


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
