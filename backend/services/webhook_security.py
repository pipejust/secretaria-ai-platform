"""Helpers para validar webhooks entrantes (Fireflies) — multi-tenant.

Cada tenant tiene su propio `webhook_token` en
`IntegrationSetting(provider_name='fireflies', tenant_id=X).config_json.webhook_token`.
La URL pública del webhook lleva `?token=...`. El backend itera todos los
tokens activos y devuelve el `tenant_id` cuya integración corresponde — eso
permite que cada empresa cliente tenga su propia URL pública sin colisiones.
"""

import hmac
import json
import logging
import os
from typing import Optional

from fastapi import HTTPException, Request, status
from sqlmodel import Session, select

from models import IntegrationSetting

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    return os.getenv("ENVIRONMENT", "").lower() in ("prod", "production")


def _resolve_tenant_by_token(db: Session, token: str) -> Optional[int]:
    """Devuelve el `tenant_id` cuya integración Fireflies tiene ese webhook_token,
    o None si ninguna lo tiene.
    """
    if not token:
        return None
    rows = db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.provider_name == "fireflies")
        .where(IntegrationSetting.is_active == True)  # noqa: E712
    ).all()
    for s in rows:
        try:
            cfg = json.loads(s.config_json or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        expected = str(cfg.get("webhook_token") or "").strip()
        if expected and hmac.compare_digest(expected, token):
            return s.tenant_id
    return None


async def verify_fireflies_webhook(request: Request, db: Session) -> int:
    """Verifica el `?token=...` y devuelve el `tenant_id` al que pertenece.

    En producción se exige token válido. En desarrollo, si NO hay ninguna
    integración Fireflies guardada todavía, devolvemos el tenant default
    para que se pueda probar el flujo end-to-end localmente.
    """
    received = (request.query_params.get("token") or "").strip()

    # Caso desarrollo sin token configurado en ningún tenant.
    has_any_token = bool(db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.provider_name == "fireflies")
        .where(IntegrationSetting.is_active == True)  # noqa: E712
    ).first())

    if not has_any_token:
        if _is_production():
            logger.error("Sin integración Fireflies en ningún tenant. Rechazando webhook.")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Webhook not configured.",
            )
        logger.warning(
            "Sin webhook_token configurado en ningún tenant. Cayendo al tenant default ('acten')."
        )
        from database import DEFAULT_TENANT_SLUG
        from models import Tenant
        t = db.exec(select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)).first()
        if not t:
            raise HTTPException(status_code=503, detail="Default tenant missing.")
        return t.id

    if not received:
        logger.warning("Webhook de Fireflies sin query param `token`.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook token.")

    tenant_id = _resolve_tenant_by_token(db, received)
    if tenant_id is None:
        logger.warning("Webhook de Fireflies con token desconocido.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token.")
    return tenant_id
