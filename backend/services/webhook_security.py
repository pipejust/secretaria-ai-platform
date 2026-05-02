"""Helpers para validar webhooks entrantes (Fireflies).

Fireflies no firma sus webhooks con HMAC; lo que sí permite es que la URL
del webhook sea libre. Aprovechamos eso: incluimos un token aleatorio como
query param (`?token=...`) en la URL que el usuario pega en Fireflies, y
validamos ese token contra el valor persistido en `IntegrationSetting`
(provider_name='fireflies', config_json.webhook_token).
"""

import hmac
import json
import logging
import os

from fastapi import HTTPException, Request, status
from sqlmodel import Session

from crud.crud_integration_setting import integration_setting as integration_setting_crud

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() in ("prod", "production")


def _load_fireflies_token(db: Session) -> str:
    """Lee el `webhook_token` desde IntegrationSetting(provider_name='fireflies')."""
    setting = integration_setting_crud.get_by_provider(
        session=db, provider_name="fireflies"
    )
    if not setting or not setting.is_active:
        return ""
    try:
        cfg = json.loads(setting.config_json or "{}")
    except (json.JSONDecodeError, TypeError):
        logger.warning("config_json inválido en IntegrationSetting(fireflies)")
        return ""
    return str(cfg.get("webhook_token") or "").strip()


async def verify_fireflies_webhook(request: Request, db: Session) -> None:
    """
    Verifica que el POST entrante traiga `?token=...` igual al persistido en BD.

    En producción se exige token. En desarrollo, si la integración aún no
    se ha guardado en `/admin/settings`, se permite el paso pero se loguea
    una advertencia.
    """
    expected = _load_fireflies_token(db)

    if not expected:
        if _is_production():
            logger.error(
                "webhook_token no configurado para Fireflies en IntegrationSetting. "
                "Rechazando webhook."
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook not configured.",
            )
        logger.warning(
            "webhook_token no configurado para Fireflies. Permitiendo webhook "
            "sin verificación (solo desarrollo)."
        )
        return

    received = (request.query_params.get("token") or "").strip()
    if not received:
        logger.warning("Webhook de Fireflies recibido sin query param `token`.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook token.",
        )

    if not hmac.compare_digest(expected, received):
        logger.warning("Token de webhook de Fireflies inválido.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook token.",
        )
