"""Branding / White-label.

La plataforma siempre se llama **Acten** (`platform_name`, no editable).
Lo que cambia por organización vive en `IntegrationSetting('branding').config_json`
y se sobreescribe sobre los defaults de abajo.

Diseño:
- Persistimos como JSON en una sola fila (`provider_name='branding'`) para
  no añadir tabla nueva ni migrar schema.
- Logos como data URLs (`data:image/png;base64,...`) embebidos en el JSON.
  Es práctico para servir desde cualquier lugar (HTML, emails, frontend) y
  evita montar un volumen de assets.
- `get_branding(db)` devuelve siempre un dict completo con los keys esperados,
  rellenando con defaults los que falten.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlmodel import Session, select

from models import IntegrationSetting

logger = logging.getLogger(__name__)

PROVIDER_NAME = "branding"

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


def get_branding(db: Session) -> dict[str, Any]:
    """Devuelve la configuración de branding completa (defaults + overrides DB)."""
    merged = dict(DEFAULT_BRANDING)
    row = db.exec(
        select(IntegrationSetting).where(IntegrationSetting.provider_name == PROVIDER_NAME)
    ).first()
    if row and row.config_json:
        try:
            stored = json.loads(row.config_json)
            if isinstance(stored, dict):
                # Solo aceptamos las keys que conocemos para evitar contaminación.
                for k, v in stored.items():
                    if k in DEFAULT_BRANDING:
                        merged[k] = v
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("branding config_json inválido, usando defaults: %s", exc)
    return merged


def update_branding(db: Session, patch: dict[str, Any]) -> dict[str, Any]:
    """Aplica un patch parcial sobre la configuración (sin tocar PROTECTED_KEYS)."""
    current = get_branding(db)
    for k, v in patch.items():
        if k in PROTECTED_KEYS:
            continue  # silencioso: ignora intentos de overridear platform_name
        if k not in DEFAULT_BRANDING:
            continue
        current[k] = v

    # No persistimos las keys de plataforma (siempre vienen de DEFAULT_BRANDING).
    to_store = {k: v for k, v in current.items() if k not in PROTECTED_KEYS}

    row = db.exec(
        select(IntegrationSetting).where(IntegrationSetting.provider_name == PROVIDER_NAME)
    ).first()
    if row:
        row.config_json = json.dumps(to_store, ensure_ascii=False)
        row.is_active = True
        db.add(row)
    else:
        db.add(
            IntegrationSetting(
                provider_name=PROVIDER_NAME,
                config_json=json.dumps(to_store, ensure_ascii=False),
                is_active=True,
            )
        )
    db.commit()
    return current
