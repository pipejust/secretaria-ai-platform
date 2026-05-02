"""Helpers para validar firmas HMAC de webhooks entrantes (Fireflies, etc.)."""

import hashlib
import hmac
import logging
import os

from fastapi import HTTPException, Request, status

from config import settings

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() in ("prod", "production")


async def verify_fireflies_signature(request: Request, body: bytes) -> None:
    """
    Verifica la firma HMAC-SHA256 que Fireflies envía en el header
    `X-Hub-Signature` o `X-Fireflies-Signature`.

    En producción se exige firma. En local, si `FIREFLIES_WEBHOOK_SECRET`
    no está configurado, se permite el paso pero se loguea una advertencia.
    """
    secret = settings.fireflies_webhook_secret or os.getenv(
        "FIREFLIES_WEBHOOK_SECRET", ""
    )

    if not secret:
        if _is_production():
            logger.error(
                "FIREFLIES_WEBHOOK_SECRET no configurado en producción. "
                "Rechazando webhook."
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook signing not configured.",
            )
        logger.warning(
            "FIREFLIES_WEBHOOK_SECRET no configurado. Permitiendo webhook "
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

    # Algunos proveedores prefijan con "sha256="
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
