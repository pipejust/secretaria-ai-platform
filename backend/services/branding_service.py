"""Branding / White-label per-tenant.

La plataforma siempre se llama **Acten** (`platform_name`, no editable).
Lo que cambia por empresa cliente vive en `Tenant.branding_json` y se
sobreescribe sobre los defaults de abajo.

Diseño:
- Persistimos como JSON en una columna de `tenant` (no fila singleton).
  Cada empresa tiene su marca; ningún query puede mezclarlas.
- Logos como data URLs (`data:image/png;base64,...`) embebidos en el JSON.
  Práctico para servir desde cualquier lugar (HTML, emails, frontend) y
  evita montar un volumen de assets.
- `get_branding(db, tenant_id)` devuelve siempre un dict completo con los
  keys esperados, rellenando con defaults los que falten.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlmodel import Session

from models import Tenant

logger = logging.getLogger(__name__)


DEFAULT_BRANDING: dict[str, Any] = {
    # Plataforma — NO se sobreescribe (es la marca del SaaS).
    "platform_name": "Acten",
    "platform_tagline": "Inteligencia para tus reuniones",
    # Empresa cliente — todo esto SÍ se sobreescribe vía /admin/branding.
    "company_name": "Acten",
    "company_tagline": "",
    "company_email": "",
    "company_address": "",
    "company_website": "",
    "company_phone": "",
    # Tema visual
    "primary_color": "#4F46E5",
    "secondary_color": "#06B6D4",
    "accent_color": "#10B981",
    # Assets — data URLs (puede ser '')
    "logo_data_url": "",
    "favicon_data_url": "",
}

# Estos campos NO se permiten editar vía PUT — son la marca de la plataforma.
PROTECTED_KEYS = {"platform_name", "platform_tagline"}


def _read_tenant_branding(tenant: Tenant) -> dict[str, Any]:
    if not tenant.branding_json:
        return {}
    try:
        data = json.loads(tenant.branding_json)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("branding_json inválido para tenant %s: %s", tenant.slug, exc)
        return {}


def get_branding(db: Session, tenant_id: int) -> dict[str, Any]:
    """Devuelve la configuración de branding completa (defaults + overrides DB)
    para el tenant indicado. Si el tenant no existe, devuelve los defaults
    (no rompe la UI cuando aún no hay datos).
    """
    merged = dict(DEFAULT_BRANDING)
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return merged
    # Default: el company_name arranca con el nombre legible del tenant.
    merged["company_name"] = tenant.name or merged["company_name"]
    stored = _read_tenant_branding(tenant)
    for k, v in stored.items():
        if k in DEFAULT_BRANDING:
            merged[k] = v
    return merged


def update_branding(db: Session, tenant_id: int, patch: dict[str, Any]) -> dict[str, Any]:
    """Aplica un patch parcial sobre la configuración del tenant
    (sin tocar PROTECTED_KEYS).
    """
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        raise ValueError(f"Tenant {tenant_id} no existe")

    current = get_branding(db, tenant_id)
    for k, v in patch.items():
        if k in PROTECTED_KEYS:
            continue
        if k not in DEFAULT_BRANDING:
            continue
        current[k] = v

    to_store = {k: v for k, v in current.items() if k not in PROTECTED_KEYS}
    tenant.branding_json = json.dumps(to_store, ensure_ascii=False)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return current
