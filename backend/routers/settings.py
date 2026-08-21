import json
import logging
import os
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import IntegrationSetting, PER_USER_INTEGRATION_PROVIDERS, Tenant, User
from services.cifrado import cifrar, descifrar
from routers.auth import get_current_tenant, get_current_user, require_admin

logger = logging.getLogger(__name__)


# Campos del config_json de un IntegrationSetting que NUNCA viajan al cliente
# cuando el caller no es el owner del tenant. Tokens API, secrets, etc.
# Reemplazados por el sentinel `"***"` para que el frontend pueda mostrar
# "configurado" sin filtrar el valor real.
_SENSITIVE_KEYS = frozenset({
    "apikey", "api_key", "apitoken", "api_token", "token",
    "pat", "client_secret", "webhook_token", "refresh_token",
    "access_token", "private_app_token", "integration_token",
    "bot_token",
})


def _redact_sensitive(cfg: dict) -> dict:
    """Devuelve una copia del config con campos sensibles reemplazados por
    `"***"`. Útil cuando un user no-owner ve la config compartida del
    owner: necesita los identificadores no sensibles (board_id, list_id,
    domain, email...) pero NUNCA las credenciales."""
    if not isinstance(cfg, dict):
        return cfg
    out = {}
    for k, v in cfg.items():
        if k.lower() in _SENSITIVE_KEYS and v not in (None, "", False):
            out[k] = "***"
        else:
            out[k] = v
    return out

router = APIRouter(
    prefix="/api/settings",
    tags=["Settings"],
)


def _public_base_url(request: Request) -> str:
    """Resuelve la URL pública del backend.

    Prioridad: env `PUBLIC_BASE_URL` > header `Origin`/`X-Forwarded-Host`
    > fallback al request actual. Sin trailing slash.
    """
    base = (os.getenv("PUBLIC_BASE_URL") or "").strip().rstrip("/")
    if base:
        return base

    forwarded = (request.headers.get("x-forwarded-host") or "").strip()
    if forwarded:
        proto = request.headers.get("x-forwarded-proto", "https").strip()
        return f"{proto}://{forwarded}".rstrip("/")

    return str(request.base_url).rstrip("/")


def _is_token_globally_unique(session: Session, token: str, exclude_setting_id: Optional[int]) -> bool:
    """True si ningún OTRO IntegrationSetting('fireflies') tiene ese token.

    Multi-tenant: cada empresa debe tener un webhook_token único globalmente
    para que el resolver de webhooks (`webhook_security`) sepa a qué tenant
    pertenece cada POST entrante.
    """
    if not token:
        return False
    rows = session.exec(
        select(IntegrationSetting).where(IntegrationSetting.provider_name == "fireflies")
    ).all()
    for row in rows:
        if exclude_setting_id is not None and row.id == exclude_setting_id:
            continue
        try:
            cfg = json.loads(row.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if str(cfg.get("webhook_token") or "").strip() == token:
            return False
    return True


def _gen_unique_webhook_token(session: Session, exclude_setting_id: Optional[int]) -> str:
    """Genera un token URL-safe de 32 bytes garantizado único entre tenants.

    La probabilidad de colisión con 32 bytes de entropía es ~10⁻⁷⁷, pero
    aún así verificamos para no dejar la puerta abierta a un escenario
    donde dos tenants pudieran terminar con el mismo token y un webhook
    entrante quedase mal-routeado.
    """
    for _ in range(8):
        candidate = secrets.token_urlsafe(32)
        if _is_token_globally_unique(session, candidate, exclude_setting_id):
            return candidate
    # Si tras 8 intentos el RNG falla (prácticamente imposible), abortamos.
    raise RuntimeError("No se pudo generar un webhook_token único.")


def _ensure_webhook_token(
    session: Session, fireflies_setting: Optional[IntegrationSetting]
) -> str:
    """Garantiza que la integración Fireflies del tenant tenga un webhook_token
    único y persistido. El token solo se autogenera server-side; nunca se
    acepta del cliente.
    """
    if fireflies_setting is None:
        return ""

    try:
        cfg = json.loads(fireflies_setting.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        cfg = {}

    token = str(cfg.get("webhook_token") or "").strip()
    if token and _is_token_globally_unique(session, token, fireflies_setting.id):
        return token

    # No existe O colisiona con otro tenant: regenerar.
    token = _gen_unique_webhook_token(session, fireflies_setting.id)
    cfg["webhook_token"] = token
    fireflies_setting.config_json = json.dumps(cfg)
    session.add(fireflies_setting)
    session.commit()
    session.refresh(fireflies_setting)
    logger.info(
        "webhook_token autogenerado para Fireflies del tenant %s.",
        fireflies_setting.tenant_id,
    )
    return token


@router.get("")
def get_all_settings(
    request: Request,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Devuelve las integraciones PER-TENANT del tenant actual.

    Ya NO devuelve providers per-user (trello/jira/clickup/azure/google/
    microsoft) — esos viven en /api/settings/me y los configura cada
    usuario por su cuenta. Si por alguna razón el admin pidiese estos
    providers via este endpoint, los filtramos para no exponer
    credenciales de otros usuarios.
    """
    settings_rows = session.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == tenant.id)
        .where(IntegrationSetting.user_id == None)  # noqa: E711 — per-tenant only
    ).all()
    result: Dict[str, Any] = {}
    fireflies_setting: Optional[IntegrationSetting] = None

    for s in settings_rows:
        if s.provider_name in PER_USER_INTEGRATION_PROVIDERS:
            # Defensa en profundidad: si quedaron filas legacy sin user_id
            # de un provider per-user, NO las exponemos aquí.
            continue
        try:
            cfg = json.loads(s.config_json)
            # La llave del SMTP vive cifrada; el administrador la ve como
            # la escribió. Lo guardado en claro antes pasa tal cual, así
            # que no hubo que reescribir ninguna fila a mano.
            if s.provider_name == "smtp" and cfg.get("apiKey"):
                cfg["apiKey"] = descifrar(cfg["apiKey"])
            result[s.provider_name] = cfg
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "config_json inválido en IntegrationSetting %s", s.provider_name
            )
            result[s.provider_name] = {}
        if s.provider_name == "fireflies":
            fireflies_setting = s

    # Si la integración de Fireflies ya existe, asegúrar token y exponer URL ya armada.
    token = _ensure_webhook_token(session, fireflies_setting)
    if token:
        base = _public_base_url(request)
        ff = result.setdefault("fireflies", {})
        ff["webhook_token"] = token
        ff["webhookUrl"] = f"{base}/api/webhook/fireflies?token={token}"

    return result


@router.post("")
def save_settings(
    payload: dict,
    request: Request,
    session: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Guarda integraciones PER-TENANT del tenant actual.

    Rechaza providers per-user — esos van por /api/settings/me con las
    credenciales del usuario que llama. Si el cliente manda uno, lo
    saltamos en silencio (sin romper el batch) y dejamos la nota en log.
    """
    for provider_name, config_obj in payload.items():
        if provider_name in PER_USER_INTEGRATION_PROVIDERS:
            logger.info(
                "save_settings(tenant=%s) ignoró provider per-user %r — usar /me",
                tenant.id, provider_name,
            )
            continue
        existing = session.exec(
            select(IntegrationSetting)
            .where(IntegrationSetting.provider_name == provider_name)
            .where(IntegrationSetting.tenant_id == tenant.id)
            .where(IntegrationSetting.user_id == None)  # noqa: E711
        ).first()

        is_active = (
            config_obj.get("isActive", True)
            if existing is None
            else config_obj.get("isActive", existing.is_active)
        )

        # La llave del SMTP se guarda cifrada. Estaba en claro en la base:
        # cualquiera con acceso de lectura —un backup, una consola— veía la
        # API key de Resend de cada empresa.
        #
        # `cifrar` es idempotente: volver a guardar algo ya cifrado no lo
        # cifra dos veces.
        if provider_name == "smtp" and config_obj.get("apiKey"):
            config_obj = {**config_obj, "apiKey": cifrar(config_obj["apiKey"])}

        # Para Fireflies: el `webhook_token` SOLO lo genera el servidor.
        # Nunca aceptamos el valor que envía el cliente — eso permitiría a un
        # admin hostil pegar el token de OTRA empresa y secuestrar sus
        # webhooks entrantes. Si el cliente lo manda, lo descartamos.
        if provider_name == "fireflies":
            existing_cfg = {}
            if existing:
                try:
                    existing_cfg = json.loads(existing.config_json or "{}")
                except (json.JSONDecodeError, TypeError):
                    existing_cfg = {}
            existing_token = str(existing_cfg.get("webhook_token") or "").strip()
            if existing_token and _is_token_globally_unique(session, existing_token, existing.id if existing else None):
                preserved_token = existing_token
            else:
                preserved_token = _gen_unique_webhook_token(
                    session, existing.id if existing else None
                )
            # Sanitización: quitamos cualquier intento del cliente de pisar
            # el token o de persistir la URL calculada.
            config_obj = {k: v for k, v in config_obj.items() if k not in ("webhook_token", "webhookUrl")}
            config_obj["webhook_token"] = preserved_token

        config_json_str = json.dumps(config_obj)

        if existing:
            existing.config_json = config_json_str
            existing.is_active = is_active
            session.add(existing)
        else:
            session.add(
                IntegrationSetting(
                    tenant_id=tenant.id,
                    user_id=None,
                    provider_name=provider_name,
                    config_json=config_json_str,
                    is_active=is_active,
                )
            )

    session.commit()
    return {"status": "success", "message": "Settings updated successfully"}


# ============================================================
# Integraciones per-user (Trello / Jira / ClickUp / Azure / Calendar)
# ============================================================

def _is_owner(tenant: Tenant, user: User) -> bool:
    """True si el usuario es el dueño del tenant. Tolera owner_user_id
    NULL en tenants legacy (cae a False — el switch share queda inerte
    hasta que la migración backfilleea el owner)."""
    return tenant.owner_user_id is not None and tenant.owner_user_id == user.id


@router.get("/me")
def get_my_settings(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Devuelve las integraciones de Configuración → Integraciones.

    Comportamiento según `tenant.share_integrations`:

    - **ON + soy owner**: devuelve mis integraciones (que son las
      compartidas con el resto del equipo).
    - **ON + NO soy owner**: devuelve las del owner pero con tokens
      redactados (`"***"`). El frontend las pinta en read-only para
      transparencia ("a dónde van mis tareas") sin filtrar credenciales.
    - **OFF**: devuelve las mías (modelo per-user puro — comportamiento
      anterior a este cambio).

    Siempre filtra a `PER_USER_INTEGRATION_PROVIDERS` para no exponer
    providers per-tenant (smtp/fireflies/branding) por este endpoint.
    """
    target_user_id = current_user.id
    redact = False
    if tenant.share_integrations and not _is_owner(tenant, current_user):
        if tenant.owner_user_id:
            target_user_id = tenant.owner_user_id
            redact = True
        # Si owner_user_id es NULL (no debería pasar tras backfill) caemos
        # al comportamiento per-user: target = current_user, sin redact.

    rows = session.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.tenant_id == tenant.id)
        .where(IntegrationSetting.user_id == target_user_id)
    ).all()
    result: Dict[str, Any] = {}
    for s in rows:
        if s.provider_name not in PER_USER_INTEGRATION_PROVIDERS:
            continue
        try:
            cfg = json.loads(s.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        if redact:
            cfg = _redact_sensitive(cfg)
        cfg["isActive"] = s.is_active
        result[s.provider_name] = cfg
    return result


@router.post("/me")
def save_my_settings(
    payload: dict,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Guarda las integraciones personales del usuario actual.

    Si `share_integrations=ON` y el caller NO es el owner → 403. Los
    no-owner solo pueden ver (en read-only) la config del owner.

    Si OFF → comportamiento per-user normal: guarda sobre mis filas.
    Si soy owner Y share_integrations=ON, mis filas ARE las compartidas
    con el equipo, así que se actualizan normalmente.
    """
    if tenant.share_integrations and not _is_owner(tenant, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "El dueño del tenant administra estas integraciones "
                "centralizadamente. Solo el owner puede modificarlas."
            ),
        )

    for provider_name, config_obj in payload.items():
        if provider_name not in PER_USER_INTEGRATION_PROVIDERS:
            logger.info(
                "save_my_settings(user=%s) ignoró provider non-per-user %r",
                current_user.id, provider_name,
            )
            continue

        existing = session.exec(
            select(IntegrationSetting)
            .where(IntegrationSetting.provider_name == provider_name)
            .where(IntegrationSetting.tenant_id == tenant.id)
            .where(IntegrationSetting.user_id == current_user.id)
        ).first()

        is_active = (
            config_obj.get("isActive", True)
            if existing is None
            else config_obj.get("isActive", existing.is_active)
        )
        # No persistimos el flag isActive dentro del config_json — lo
        # mantenemos solo en la columna is_active de la tabla.
        config_to_persist = {k: v for k, v in config_obj.items() if k != "isActive"}

        # Si el cliente mandó tokens redactados ("***") en un campo
        # sensible, NO los persistimos — eran placeholders del read-only
        # render. Conservamos el valor previo si existía.
        existing_cfg: dict = {}
        if existing:
            try:
                existing_cfg = json.loads(existing.config_json or "{}")
            except (json.JSONDecodeError, TypeError):
                existing_cfg = {}
        sanitized = {}
        for k, v in config_to_persist.items():
            if v == "***" and k.lower() in _SENSITIVE_KEYS and k in existing_cfg:
                sanitized[k] = existing_cfg[k]
            else:
                sanitized[k] = v
        config_json_str = json.dumps(sanitized)

        if existing:
            existing.config_json = config_json_str
            existing.is_active = is_active
            session.add(existing)
        else:
            session.add(
                IntegrationSetting(
                    tenant_id=tenant.id,
                    user_id=current_user.id,
                    provider_name=provider_name,
                    config_json=config_json_str,
                    is_active=is_active,
                )
            )

    session.commit()
    return {"status": "success", "message": "User settings updated successfully"}


@router.delete("/me/{provider_name}")
def delete_my_setting(
    provider_name: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Elimina la configuración personal del usuario para un provider.

    Si `share_integrations=ON` y caller no es owner → 403 (no puede
    desconectar la integración de la empresa). Si OFF, borra la fila
    per-user del current_user.
    """
    if tenant.share_integrations and not _is_owner(tenant, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="El dueño del tenant administra estas integraciones.",
        )
    if provider_name not in PER_USER_INTEGRATION_PROVIDERS:
        return {"status": "ignored", "reason": "not a per-user provider"}
    row = session.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.provider_name == provider_name)
        .where(IntegrationSetting.tenant_id == tenant.id)
        .where(IntegrationSetting.user_id == current_user.id)
    ).first()
    if row:
        session.delete(row)
        session.commit()
    return {"status": "success"}


# ============================================================
# Switches de "compartido vs per-user" — solo edita el owner.
# ============================================================

class ShareSettingsUpdate(BaseModel):
    """Cuerpo de PUT /api/settings/share. Campos opcionales para
    permitir patches parciales (solo `share_integrations` o solo
    `share_routings`)."""
    share_integrations: Optional[bool] = None
    share_routings: Optional[bool] = None


@router.get("/share")
def get_share_settings(
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Estado de los switches `share_*` del tenant + indicación de si
    el caller es el owner (para renderizar la UI condicional).

    Cualquier user autenticado puede leerlo — necesita saber el modelo
    para pintar la UI (read-only vs editable).
    """
    return {
        "owner_user_id": tenant.owner_user_id,
        "is_owner": _is_owner(tenant, current_user),
        "share_integrations": bool(tenant.share_integrations),
        "share_routings": bool(tenant.share_routings),
    }


@router.put("/share")
def update_share_settings(
    payload: ShareSettingsUpdate,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Modifica los switches `share_*`. Solo el owner del tenant puede.

    Cambiar de OFF→ON oculta (pero NO elimina) los datos personales de
    los demás usuarios — al volver a OFF reaparecen.
    """
    if not _is_owner(tenant, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el dueño del tenant puede modificar estos ajustes.",
        )

    if payload.share_integrations is not None:
        tenant.share_integrations = bool(payload.share_integrations)
    if payload.share_routings is not None:
        tenant.share_routings = bool(payload.share_routings)

    session.add(tenant)
    session.commit()
    session.refresh(tenant)

    return {
        "owner_user_id": tenant.owner_user_id,
        "is_owner": True,
        "share_integrations": bool(tenant.share_integrations),
        "share_routings": bool(tenant.share_routings),
    }
