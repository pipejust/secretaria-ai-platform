"""Helpers para validar firmas HMAC de webhooks entrantes (Fireflies, etc.)."""

import hashlib
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


def _load_fireflies_secret(db: Session) -> str:
    """Lee el `webhook_secret` desde IntegrationSetting(provider_name='fireflies')."""
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
    return str(cfg.get("webhook_secret") or "").strip()


async def verify_fireflies_signature(
    request: Request, body: bytes, db: Session
) -> None:
    """
    Verifica la firma HMAC-SHA256 que Fireflies envía en el header
    `X-Hub-Signature-256` (también acepta `X-Fireflies-Signature` o
    `X-Hub-Signature` por compatibilidad).

    El secreto se almacena en la tabla `IntegrationSetting`, en el registro
    con `provider_name = 'fireflies'`, dentro de `config_json.webhook_secret`.

    En producción se exige firma. En local, si no hay secreto configurado,
    se permite el paso pero se loguea una advertencia.
    """
    secret = _load_fireflies_secret(db)

    if not secret:
        if _is_production():
            logger.error(
                "webhook_secret no configurado para Fireflies en IntegrationSetting. "
                "Rechazando webhook."
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook signing not configured.",
            )
        logger.warning(
            "webhook_secret no configurado para Fireflies. Permitiendo webhook "
            "sin verificación (solo desarrollo)."
        )
        return

    received_signature = (
        request.headers.get("x-hub-signature-256")
        or request.headers.get("x-fireflies-signature")
        or request.headers.get("x-hub-signature")
        or ""
    ).strip()

    if not received_signature:
        logger.warning("Webhook recibido sin header de firma.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing webhook signature.",
        )

    if received_signature.startswith("sha256="):
        received_signature = received_signature[len("sha256="):]

    expected = hmac.new(
        key=secret.encode("utf-8"),
        msg=body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected, received_signature):
        logger.warning("Firma de webhook inválida.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature.",
        )
