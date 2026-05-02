"""
Factory que materializa servicios de integración (Trello, Jira, ClickUp,
Azure DevOps) leyendo credenciales reales desde la tabla `IntegrationSetting`
en Supabase. Reemplaza los antiguos mocks ("mock_key", "mock_token") que
estaban hardcodeados en el router de Fireflies.

Cada provider espera un `config_json` con la siguiente forma mínima:

  trello       -> {"api_key": "...", "token": "..."}
  jira         -> {"domain": "<sub>.atlassian.net", "email": "...", "api_token": "..."}
  clickup      -> {"api_token": "..."}
  azure_devops -> {"organization": "...", "project": "...", "pat": "..."}
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from sqlmodel import Session

from crud.crud_integration_setting import integration_setting as integration_setting_crud
from services.integrations.azure_devops import AzureDevOpsIntegrationService
from services.integrations.clickup import ClickUpIntegrationService
from services.integrations.jira import JiraIntegrationService
from services.integrations.trello import TrelloIntegrationService

logger = logging.getLogger(__name__)


class IntegrationConfigError(RuntimeError):
    """Se lanza cuando faltan credenciales o están malformadas para un provider."""


def _load_provider_config(db: Session, provider_name: str) -> Dict[str, Any]:
    setting = integration_setting_crud.get_by_provider(
        session=db, provider_name=provider_name
    )
    if not setting or not setting.is_active:
        raise IntegrationConfigError(
            f"Integración '{provider_name}' no configurada o desactivada en IntegrationSetting."
        )
    try:
        config = json.loads(setting.config_json or "{}")
    except json.JSONDecodeError as exc:
        raise IntegrationConfigError(
            f"config_json inválido para '{provider_name}': {exc}"
        ) from exc
    if not isinstance(config, dict):
        raise IntegrationConfigError(
            f"config_json para '{provider_name}' debe ser un objeto JSON."
        )
    return config


def _require(config: Dict[str, Any], keys: list[str], provider: str) -> None:
    missing = [k for k in keys if not config.get(k)]
    if missing:
        raise IntegrationConfigError(
            f"Faltan campos obligatorios en la configuración de '{provider}': "
            f"{', '.join(missing)}."
        )


def get_trello_service(db: Session) -> TrelloIntegrationService:
    cfg = _load_provider_config(db, "trello")
    _require(cfg, ["api_key", "token"], "trello")
    return TrelloIntegrationService(cfg["api_key"], cfg["token"])


def get_jira_service(db: Session) -> JiraIntegrationService:
    cfg = _load_provider_config(db, "jira")
    _require(cfg, ["domain", "email", "api_token"], "jira")
    return JiraIntegrationService(cfg["domain"], cfg["email"], cfg["api_token"])


def get_clickup_service(db: Session) -> ClickUpIntegrationService:
    cfg = _load_provider_config(db, "clickup")
    _require(cfg, ["api_token"], "clickup")
    return ClickUpIntegrationService(cfg["api_token"])


def get_azure_devops_service(db: Session) -> AzureDevOpsIntegrationService:
    cfg = _load_provider_config(db, "azure_devops")
    _require(cfg, ["organization", "project", "pat"], "azure_devops")
    return AzureDevOpsIntegrationService(cfg["organization"], cfg["project"], cfg["pat"])


def get_service_for_destination(
    db: Session, destination_type: str
) -> Optional[object]:
    """
    Despacha por tipo (case-insensitive) y devuelve el servicio listo para usar.
    Devuelve None si el tipo no se reconoce. Lanza IntegrationConfigError si
    el tipo se reconoce pero no hay credenciales válidas.
    """
    dt = (destination_type or "").lower()
    if "trello" in dt:
        return get_trello_service(db)
    if "jira" in dt:
        return get_jira_service(db)
    if "clickup" in dt:
        return get_clickup_service(db)
    if "azure" in dt or "devops" in dt:
        return get_azure_devops_service(db)
    logger.warning("Destination_type no reconocido: %s", destination_type)
    return None
