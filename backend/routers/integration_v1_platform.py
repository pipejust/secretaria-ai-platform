"""API pública v1 — conectar plataformas desde la interfaz del cliente.

Lo que Acten hace por dentro (mandar las tareas a Jira, publicar el
resumen en Slack, enganchar una agenda de Google) tiene que poder
configurarse **sin entrar a Acten**, porque el acuerdo es justamente que
sus usuarios nunca entran. Este módulo expone esa configuración.

Dos familias:

* **Integraciones y rutas** — a dónde va cada sesión. Las credenciales se
  guardan aquí y **nunca se devuelven**: se responde qué campos hay
  puestos, no su valor. Una API que te deja leer el token de Jira que
  escribiste ayer es una filtración esperando a que alguien reutilice la
  clave.
* **Calendario** — el OAuth vive en el navegador del empleado, así que no
  puede depender de una sesión de Acten. Se resuelve con un `state`
  firmado de vida corta; ver `calendar_connect`.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import (
    IntegrationSetting,
    MeetingSession,
    PER_USER_INTEGRATION_PROVIDERS,
    Project,
    Routing,
    User,
)
from services.api_key_auth import IntegrationContext, require_scopes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Integración v1 — plataformas"])


# ══════════════════════════════════════════════════════════════════════
# CATÁLOGO
# ══════════════════════════════════════════════════════════════════════

# `campos` son las claves que espera cada servicio en su `config_json`.
# `ambito` decide dónde se guarda: 'usuario' → credencial de la persona;
# 'empresa' → una sola para todo el tenant.
# `efecto` es lo que hace al procesarse una sesión — importa para que la
# interfaz no ofrezca «Slack» esperando una tarjeta por tarea.
PROVIDERS: list[dict[str, Any]] = [
    {"id": "trello",     "nombre": "Trello",           "familia": "tareas",
     "ambito": "usuario", "campos": ["api_key", "token"],
     "efecto": "una tarjeta por tarea"},
    {"id": "jira",       "nombre": "Jira",             "familia": "tareas",
     "ambito": "usuario", "campos": ["domain", "email", "api_token"],
     "efecto": "una incidencia por tarea"},
    {"id": "clickup",    "nombre": "ClickUp",          "familia": "tareas",
     "ambito": "usuario", "campos": ["api_token"],
     "efecto": "una tarea por tarea"},
    {"id": "azure",      "nombre": "Azure DevOps",     "familia": "tareas",
     "ambito": "usuario", "campos": ["organization", "project", "pat"],
     "efecto": "un work item por tarea"},
    {"id": "slack",      "nombre": "Slack",            "familia": "mensajería",
     "ambito": "empresa", "campos": ["webhook_url", "bot_token"],
     "efecto": "un mensaje con el resumen de la sesión"},
    {"id": "teams",      "nombre": "Microsoft Teams",  "familia": "mensajería",
     "ambito": "empresa", "campos": ["webhook_url"],
     "efecto": "una tarjeta con el resumen de la sesión"},
    {"id": "notion",     "nombre": "Notion",           "familia": "documentos",
     "ambito": "empresa", "campos": ["integration_token", "database_id"],
     "efecto": "una página por sesión"},
    {"id": "gdocs",      "nombre": "Google Docs",      "familia": "documentos",
     "ambito": "empresa", "campos": ["access_token", "refresh_token"],
     "efecto": "un documento por sesión"},
    {"id": "hubspot",    "nombre": "HubSpot",          "familia": "crm",
     "ambito": "empresa", "campos": ["private_app_token"],
     "efecto": "nota en el negocio del asistente"},
    {"id": "salesforce", "nombre": "Salesforce",       "familia": "crm",
     "ambito": "empresa", "campos": ["instance_url", "access_token"],
     "efecto": "nota en la oportunidad del asistente"},
    {"id": "pipedrive",  "nombre": "Pipedrive",        "familia": "crm",
     "ambito": "empresa", "campos": ["api_token", "company_domain"],
     "efecto": "nota en el trato del asistente"},
]
POR_ID = {p["id"]: p for p in PROVIDERS}

# El id del catálogo no siempre es el nombre con el que la credencial se
# guarda: el despachador busca `microsoft_teams` y `google_docs`. Si se
# guarda con otro nombre la integración queda «conectada» en pantalla y
# muda en la práctica, que es el peor de los fallos posibles.
ALMACEN_POR_ID = {
    "teams": "microsoft_teams",
    "gdocs": "google_docs",
}


def _clave_almacen(provider_id: str) -> str:
    return ALMACEN_POR_ID.get(provider_id, provider_id)

# El `destination_type` que guarda `Routing` es el nombre con el que el
# despachador hace match, no el id del catálogo.
DESTINO_POR_ID = {
    "trello": "Trello", "jira": "Jira", "clickup": "ClickUp",
    "azure": "Azure DevOps", "slack": "Slack", "teams": "Teams",
    "notion": "Notion", "gdocs": "GDocs", "hubspot": "HubSpot",
    "salesforce": "Salesforce", "pipedrive": "Pipedrive",
}


@router.get("/integrations/providers")
def list_providers(
    ctx: IntegrationContext = Depends(require_scopes("integrations:read")),
):
    """Catálogo de plataformas conectables, para pintar el formulario."""
    return {"items": PROVIDERS, "total": len(PROVIDERS)}


# ══════════════════════════════════════════════════════════════════════
# CREDENCIALES
# ══════════════════════════════════════════════════════════════════════

def _actor(ctx: IntegrationContext, db: Session) -> User:
    """Persona en cuyo nombre se configura. Sin ella no hay credencial
    per-usuario posible: guardar el token de Jira «de la empresa» haría
    que las tareas de todos aparecieran creadas por la misma cuenta."""
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


def _setting_query(provider: str, ctx: IntegrationContext, user_id: Optional[int]):
    q = (
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == ctx.tenant.id)
        .where(IntegrationSetting.provider_name == provider)
    )
    if user_id is None:
        return q.where(IntegrationSetting.user_id == None)  # noqa: E711
    return q.where(IntegrationSetting.user_id == user_id)


def _scope_user_id(provider: str, ctx: IntegrationContext, db: Session) -> Optional[int]:
    if provider in PER_USER_INTEGRATION_PROVIDERS:
        return _actor(ctx, db).id
    return None


@router.get("/integrations")
def list_integrations(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:read")),
):
    """Qué está conectado. **Nunca devuelve los secretos.**

    Por cada plataforma se informa si está configurada, qué campos tienen
    valor y cuáles faltan. Con eso la interfaz puede mostrar «Jira ·
    conectado» y un formulario que pida solo lo que falta, sin que el
    token viaje de vuelta al navegador.
    """
    out = []
    for p in PROVIDERS:
        uid = (
            ctx.acting_user.id
            if (p["ambito"] == "usuario" and ctx.acting_user) else None
        )
        almacen = _clave_almacen(p["id"])
        row = db.exec(_setting_query(almacen, ctx, uid)).first()
        cfg: dict = {}
        if row:
            try:
                cfg = json.loads(row.config_json or "{}")
            except (json.JSONDecodeError, TypeError):
                cfg = {}
            # Las filas guardadas desde la interfaz de Acten vienen en
            # camelCase; sin normalizar, algo conectado se reportaría
            # como «faltan todos los campos».
            from services.integrations import normalizar_config
            cfg = normalizar_config(cfg, almacen)
        puestos = [c for c in p["campos"] if str(cfg.get(c) or "").strip()]
        out.append({
            "id": p["id"],
            "nombre": p["nombre"],
            "familia": p["familia"],
            "ambito": p["ambito"],
            "configurado": bool(puestos) and (row.is_active if row else False),
            "activo": bool(row.is_active) if row else False,
            "campos_puestos": puestos,
            "campos_faltantes": [c for c in p["campos"] if c not in puestos],
        })
    return {"items": out, "total": len(out)}


class IntegrationIn(BaseModel):
    config: dict[str, str]
    activo: bool = True


@router.put("/integrations/{provider}")
def save_integration(
    provider: str,
    payload: IntegrationIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:write")),
):
    """Guarda (o actualiza) las credenciales de una plataforma.

    Los campos vacíos **no borran** lo que ya había: la interfaz muestra
    el token enmascarado y reenviar la máscara no debe tirar la
    credencial buena.
    """
    p = POR_ID.get(provider)
    if not p:
        raise HTTPException(
            422, f"Plataforma desconocida: '{provider}'. Ver /integrations/providers.",
        )
    desconocidos = set(payload.config) - set(p["campos"])
    if desconocidos:
        raise HTTPException(
            422,
            f"Campos que {p['nombre']} no usa: {sorted(desconocidos)}. "
            f"Espera: {p['campos']}.",
        )

    uid = _scope_user_id(provider, ctx, db)
    almacen = _clave_almacen(provider)
    row = db.exec(_setting_query(almacen, ctx, uid)).first()
    cfg: dict = {}
    if row:
        try:
            cfg = json.loads(row.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            cfg = {}
    for k, v in payload.config.items():
        if str(v or "").strip():
            cfg[k] = v

    if row:
        row.config_json = json.dumps(cfg)
        row.is_active = payload.activo
    else:
        row = IntegrationSetting(
            tenant_id=ctx.tenant.id, user_id=uid,
            provider_name=almacen, config_json=json.dumps(cfg),
            is_active=payload.activo,
        )
    db.add(row); db.commit(); db.refresh(row)

    faltan = [c for c in p["campos"] if not str(cfg.get(c) or "").strip()]
    return {
        "id": provider, "activo": row.is_active,
        "ambito": p["ambito"],
        "campos_puestos": [c for c in p["campos"] if c not in faltan],
        "campos_faltantes": faltan,
        "listo": not faltan or provider == "slack",  # Slack acepta uno u otro
    }


@router.delete("/integrations/{provider}")
def delete_integration(
    provider: str,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:write")),
):
    """Desconecta la plataforma y borra sus credenciales."""
    if provider not in POR_ID:
        raise HTTPException(422, f"Plataforma desconocida: '{provider}'.")
    uid = _scope_user_id(provider, ctx, db)
    row = db.exec(_setting_query(_clave_almacen(provider), ctx, uid)).first()
    if not row:
        return {"status": "no_estaba"}
    db.delete(row); db.commit()
    return {"status": "borrado"}


# ══════════════════════════════════════════════════════════════════════
# RUTAS POR PROYECTO — a dónde va cada sesión
# ══════════════════════════════════════════════════════════════════════

def _proyecto(db: Session, ctx: IntegrationContext, external_ref: str) -> Project:
    p = db.exec(
        select(Project)
        .where(Project.tenant_id == ctx.tenant.id)
        .where(Project.external_ref == external_ref)
    ).first()
    if not p:
        raise HTTPException(422, f"No existe un proyecto sincronizado con id '{external_ref}'.")
    if ctx.on_behalf_of and p.id not in (ctx.visible_project_ids or []):
        raise HTTPException(404, "Proyecto no encontrado.")
    return p


def _routing_user_id(ctx: IntegrationContext, db: Session) -> int:
    """Dueño de las rutas. Con `share_routings` las administra el owner
    del tenant; sin él, cada persona las suyas."""
    t = ctx.tenant
    if t.share_routings and t.owner_user_id:
        return t.owner_user_id
    return _actor(ctx, db).id


def _serialize_routing(r: Routing) -> dict[str, Any]:
    try:
        cfg = json.loads(r.destination_config or "{}")
    except (json.JSONDecodeError, TypeError):
        cfg = {}
    return {
        "id": r.id, "destino": r.destination_type,
        "config": cfg, "activo": r.is_active,
    }


@router.get("/projects/{project_external_id}/routings")
def list_routings(
    project_external_id: str,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:read")),
):
    """A dónde se envía lo que sale de las sesiones de ese proyecto."""
    proj = _proyecto(db, ctx, project_external_id)
    uid = _routing_user_id(ctx, db)
    rows = db.exec(
        select(Routing)
        .where(Routing.project_id == proj.id)
        .where(Routing.user_id == uid)
    ).all()
    return {"items": [_serialize_routing(r) for r in rows], "total": len(rows)}


class RoutingIn(BaseModel):
    destino: str
    config: dict[str, Any] = {}
    activo: bool = True


@router.post("/projects/{project_external_id}/routings", status_code=201)
def create_routing(
    project_external_id: str,
    payload: RoutingIn,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:write")),
):
    proj = _proyecto(db, ctx, project_external_id)
    destino = DESTINO_POR_ID.get(payload.destino.lower(), payload.destino)
    if destino not in DESTINO_POR_ID.values():
        raise HTTPException(
            422,
            f"Destino desconocido: '{payload.destino}'. Ver /integrations/providers.",
        )
    r = Routing(
        project_id=proj.id,
        user_id=_routing_user_id(ctx, db),
        destination_type=destino,
        destination_config=json.dumps(payload.config or {}),
        is_active=payload.activo,
    )
    db.add(r); db.commit(); db.refresh(r)
    return _serialize_routing(r)


class RoutingPatch(BaseModel):
    config: Optional[dict[str, Any]] = None
    activo: Optional[bool] = None


def _routing_o_404(db: Session, ctx: IntegrationContext, routing_id: int) -> Routing:
    r = db.get(Routing, routing_id)
    if not r:
        raise HTTPException(404, "Ruta no encontrada.")
    proj = db.get(Project, r.project_id)
    if not proj or proj.tenant_id != ctx.tenant.id:
        raise HTTPException(404, "Ruta no encontrada.")
    if r.user_id != _routing_user_id(ctx, db):
        raise HTTPException(404, "Ruta no encontrada.")
    return r


@router.patch("/routings/{routing_id}")
def patch_routing(
    routing_id: int,
    payload: RoutingPatch,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:write")),
):
    r = _routing_o_404(db, ctx, routing_id)
    if payload.config is not None:
        r.destination_config = json.dumps(payload.config)
    if payload.activo is not None:
        r.is_active = payload.activo
    db.add(r); db.commit(); db.refresh(r)
    return _serialize_routing(r)


@router.delete("/routings/{routing_id}")
def delete_routing(
    routing_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("integrations:write")),
):
    r = _routing_o_404(db, ctx, routing_id)
    db.delete(r); db.commit()
    return {"status": "borrado"}


# ══════════════════════════════════════════════════════════════════════
# CALENDARIO — conectar una agenda sin pasar por Acten
# ══════════════════════════════════════════════════════════════════════

# El OAuth ocurre en el navegador del empleado y vuelve a Acten por una
# redirección: ahí no hay sesión de Acten, porque el acuerdo es que esa
# gente nunca entra. Se ata con un `state` firmado y de vida corta que
# dice a qué usuario pertenece el permiso concedido.
CONNECT_TTL_MIN = 15
CONNECT_PURPOSE = "calendar_connect"


def firmar_state(user_id: int, tenant_id: int, provider: str) -> str:
    from auth_utils import create_access_token
    return create_access_token(
        {
            "purpose": CONNECT_PURPOSE, "uid": user_id,
            "tid": tenant_id, "provider": provider,
        },
        expires_delta=timedelta(minutes=CONNECT_TTL_MIN),
    )


def leer_state(state: str) -> Optional[dict]:
    """Devuelve el contenido si el `state` es nuestro y no ha caducado."""
    from jose import JWTError, jwt
    from auth_utils import ALGORITHM, SECRET_KEY
    try:
        data = jwt.decode(state, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        return None
    if data.get("purpose") != CONNECT_PURPOSE:
        return None
    return data


@router.post("/calendar/connect")
def calendar_connect(
    provider: str = Query(..., pattern="^(google|microsoft)$"),
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("calendar:write")),
):
    """Devuelve la URL a la que mandar al empleado para conectar su agenda.

    Ábranla en una pestaña. Al aceptar, el proveedor redirige a Acten, que
    valida el `state` firmado y guarda la cuenta a nombre de esa persona.
    El enlace caduca en 15 minutos: un enlace de conexión eterno es un
    enlace que alguien reenvía por chat y termina conectando la agenda
    equivocada.
    """
    from routers.calendar import _load_oauth_cfg
    from services.calendar_service import google_oauth_url, microsoft_oauth_url

    user = _actor(ctx, db)
    state = firmar_state(user.id, ctx.tenant.id, provider)
    cfg = _load_oauth_cfg(provider, ctx.tenant.id, db)
    constructor = google_oauth_url if provider == "google" else microsoft_oauth_url
    try:
        url = constructor(state, cfg=cfg)
    except RuntimeError as exc:
        raise HTTPException(
            503,
            f"El OAuth de {provider} no está configurado en Acten: {exc}",
        )
    return {
        "auth_url": url,
        "provider": provider,
        "expira_en_min": CONNECT_TTL_MIN,
        "empleado": user.email,
    }


@router.get("/calendar/accounts")
def calendar_accounts(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("calendar:read")),
):
    """Agendas conectadas por esa persona."""
    from models import CalendarAccount

    user = _actor(ctx, db)
    rows = db.exec(
        select(CalendarAccount).where(CalendarAccount.user_id == user.id)
    ).all()
    return {
        "items": [
            {
                "id": a.id, "provider": a.provider, "correo": a.account_email,
                "activa": a.is_active, "conectada_el": a.created_at,
            }
            for a in rows
        ],
        "total": len(rows),
    }


@router.delete("/calendar/accounts/{account_id}")
def calendar_disconnect(
    account_id: int,
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("calendar:write")),
):
    from models import CalendarAccount

    user = _actor(ctx, db)
    acc = db.get(CalendarAccount, account_id)
    if not acc or acc.user_id != user.id:
        raise HTTPException(404, "Cuenta no encontrada.")
    db.delete(acc); db.commit()
    return {"status": "desconectada"}


@router.post("/calendar/sync")
async def calendar_sync(
    db: Session = Depends(get_session),
    ctx: IntegrationContext = Depends(require_scopes("calendar:write")),
):
    """Trae los eventos de las agendas conectadas por esa persona.

    Normalmente no hace falta llamarla: Acten sincroniza por su cuenta.
    Está para el botón «actualizar ahora» de su interfaz.
    """
    from routers.calendar import sync_now

    user = _actor(ctx, db)
    return await sync_now(current_user=user, db=db)
