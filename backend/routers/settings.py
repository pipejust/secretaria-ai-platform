import json
import logging
import os
import secrets
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session, select

from database import get_session
from models import IntegrationSetting, Tenant, User
from routers.auth import get_current_tenant, require_admin

logger = logging.getLogger(__name__)

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
    """Devuelve la configuración de todas las integraciones del tenant actual."""
    settings_rows = session.exec(
        select(IntegrationSetting).where(IntegrationSetting.tenant_id == tenant.id)
    ).all()
    result: Dict[str, Any] = {}
    fireflies_setting: Optional[IntegrationSetting] = None

    for s in settings_rows:
        try:
            result[s.provider_name] = json.loads(s.config_json)
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
    """Crea o actualiza configuración de integraciones del tenant actual."""
    for provider_name, config_obj in payload.items():
        existing = session.exec(
            select(IntegrationSetting)
            .where(IntegrationSetting.provider_name == provider_name)
            .where(IntegrationSetting.tenant_id == tenant.id)
        ).first()

        is_active = (
            config_obj.get("isActive", True)
            if existing is None
            else config_obj.get("isActive", existing.is_active)
        )

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
                    provider_name=provider_name,
                    config_json=config_json_str,
                    is_active=is_active,
                )
            )

    session.commit()
    return {"status": "success", "message": "Settings updated successfully"}
