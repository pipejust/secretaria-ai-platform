"""Seed para desarrollo local. Idempotente.

Crea:
- Rol 'admin'
- Usuario admin@notiva.local / notiva
- Plantillas de OutputTemplate (Sprint 04)
- Configuración mínima de IntegrationSetting (sin tokens reales)

Uso:
    cd backend && python scripts/seed_dev.py
    # o automático en startup si ENVIRONMENT=development y --seed-on-startup=true
"""

from __future__ import annotations

import json
import logging
import os
import sys

sys.path.insert(0, ".")

from sqlmodel import Session, select

from auth_utils import get_password_hash
from database import engine
from models import IntegrationSetting, OutputTemplate, Role, User

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("seed_dev")


DEFAULT_TEMPLATES = [
    {
        "name": "Deal Brief",
        "role_type": "commercial",
        "prompt_template": (
            "Eres un analista de ventas. A partir de la transcripción de la "
            "reunión genera un Deal Brief en Markdown con secciones:\n"
            "- Cliente / Empresa\n- Necesidad detectada\n- Stakeholders y rol\n"
            "- Pains identificados\n- Oferta acordada (si la hubo)\n"
            "- Próximos pasos con owner y fecha\n- Riesgos del deal\n\n"
            "Transcripción:\n{{transcript}}"
        ),
    },
    {
        "name": "PRD",
        "role_type": "product",
        "prompt_template": (
            "Eres product manager senior. Genera un PRD breve en Markdown con:\n"
            "## Problema\n## Usuario objetivo\n## Solución propuesta\n"
            "## Criterios de éxito (métricas)\n## Scope MVP\n"
            "## Out of scope\n## Riesgos / open questions\n\n"
            "Transcripción:\n{{transcript}}"
        ),
    },
    {
        "name": "Status Update",
        "role_type": "status",
        "prompt_template": (
            "Genera un Status Update conciso en Markdown:\n"
            "## ✅ Logrado en este período\n## 🚧 En progreso\n"
            "## ⛔ Bloqueos\n## 🎯 Próximos hitos\n\n"
            "Transcripción:\n{{transcript}}"
        ),
    },
    {
        "name": "1:1 Notes",
        "role_type": "hr",
        "prompt_template": (
            "Genera notas de 1:1 con tono empático y humano:\n"
            "## Cómo viene la persona\n## Wins recientes\n## Frustraciones\n"
            "## Crecimiento profesional\n## Action items para manager\n"
            "## Action items para colaborador\n\n"
            "Transcripción:\n{{transcript}}"
        ),
    },
    {
        "name": "Kickoff Document",
        "role_type": "kickoff",
        "prompt_template": (
            "Genera un Kickoff Document de proyecto:\n"
            "## Objetivos del proyecto\n## Stakeholders y roles\n"
            "## Hitos y fechas clave\n## Definition of Done\n"
            "## Riesgos identificados\n## Cadencia de seguimiento\n\n"
            "Transcripción:\n{{transcript}}"
        ),
    },
    {
        "name": "Evaluation Report",
        "role_type": "eval",
        "prompt_template": (
            "Genera un Evaluation Report (post-implementación o retrospectiva):\n"
            "## Lo que funcionó\n## Lo que NO funcionó\n## Causa raíz\n"
            "## Métricas observadas\n## Recomendaciones a futuro\n"
            "## Action items\n\n"
            "Transcripción:\n{{transcript}}"
        ),
    },
]


def seed_role_and_admin(db: Session) -> None:
    role = db.exec(select(Role).where(Role.name == "admin")).first()
    if not role:
        role = Role(name="admin", description="Administrador")
        db.add(role); db.commit(); db.refresh(role)
        log.info("Rol 'admin' creado.")
    user = db.exec(select(User).where(User.email == "admin@notiva.local")).first()
    if not user:
        user = User(
            email="admin@notiva.local",
            hashed_password=get_password_hash("notiva"),
            full_name="Admin Local",
            is_active=True,
            role_id=role.id,
        )
        db.add(user); db.commit()
        log.info("Usuario admin@notiva.local creado (password: notiva).")
    else:
        log.info("Usuario admin@notiva.local ya existe.")


def seed_output_templates(db: Session) -> None:
    for tpl in DEFAULT_TEMPLATES:
        existing = db.exec(
            select(OutputTemplate).where(OutputTemplate.name == tpl["name"])
        ).first()
        if existing:
            continue
        db.add(OutputTemplate(**tpl))
    db.commit()
    log.info("Plantillas de outputs verificadas/creadas.")


def seed_default_integrations(db: Session) -> None:
    """Pre-crear los settings vacíos para que el frontend muestre los formularios."""
    defaults = [
        ("smtp", {"provider": "Resend", "apiKey": "", "senderEmail": "no-reply@acten.local"}),
        ("fireflies", {"apiKey": ""}),
        ("trello", {"api_key": "", "token": "", "isActive": False}),
        ("jira", {"email": "", "api_token": "", "domain": "", "isActive": False}),
        ("clickup", {"api_token": "", "isActive": False}),
        ("azure_devops", {"organization": "", "project": "", "pat": "", "isActive": False}),
        ("autoCuration", {"isEnabled": False, "timeoutHours": 24}),
        # White-label: defaults pre-poblados para que /admin/branding muestre
        # algo en una instalación fresca. NO incluye platform_name (lo añade
        # `branding_service.get_branding` desde DEFAULT_BRANDING).
        (
            "branding",
            {
                "company_name": "Acten",
                "company_tagline": "",
                "company_email": "",
                "company_address": "",
                "company_website": "",
                "company_phone": "",
                "primary_color": "#4F46E5",
                "secondary_color": "#06B6D4",
                "accent_color": "#10B981",
                "logo_data_url": "",
                "favicon_data_url": "",
            },
        ),
    ]
    for name, cfg in defaults:
        existing = db.exec(
            select(IntegrationSetting).where(IntegrationSetting.provider_name == name)
        ).first()
        if existing:
            continue
        db.add(IntegrationSetting(provider_name=name, config_json=json.dumps(cfg), is_active=True))
    db.commit()
    log.info("IntegrationSetting defaults creadas.")


def main() -> int:
    with Session(engine) as db:
        seed_role_and_admin(db)
        seed_output_templates(db)
        seed_default_integrations(db)
    log.info("Seed completado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
