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


def _ensure_webhook_token(
    session: Session, fireflies_setting: Optional[IntegrationSetting]
) -> str:
    """Garantiza que la integración Fireflies tenga un webhook_token persistido.

    - Si el setting no existe, devuelve "" (no lo creamos vacío; espera al POST).
    - Si existe pero no trae token, genera uno seguro y lo guarda.
    """
    if fireflies_setting is None:
        return ""

    try:
        cfg = json.loads(fireflies_setting.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        cfg = {}

    token = str(cfg.get("webhook_token") or "").strip()
    if token:
        return token

    token = secrets.token_urlsafe(32)
    cfg["webhook_token"] = token
    fireflies_setting.config_json = json.dumps(cfg)
    session.add(fireflies_setting)
    session.commit()
    session.refresh(fireflies_setting)
    logger.info("webhook_token autogenerado para integración Fireflies.")
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

        # Para Fireflies: nunca dejamos pisar el webhook_token con un valor vacío
        # ni con la `webhookUrl` calculada que el frontend nos devuelve.
        if provider_name == "fireflies":
            existing_cfg = {}
            if existing:
                try:
                    existing_cfg = json.loads(existing.config_json or "{}")
                except (json.JSONDecodeError, TypeError):
                    existing_cfg = {}
            preserved_token = (
                str(config_obj.get("webhook_token") or "").strip()
                or str(existing_cfg.get("webhook_token") or "").strip()
                or secrets.token_urlsafe(32)
            )
            config_obj = {**config_obj, "webhook_token": preserved_token}
            # No persistimos la URL completa, se calcula al vuelo en GET.
            config_obj.pop("webhookUrl", None)

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
