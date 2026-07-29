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
# Sprint 05 — distribuciones
from services.integrations.slack import SlackIntegrationService
from services.integrations.notion import NotionIntegrationService
from services.integrations.microsoft_teams import MicrosoftTeamsIntegrationService
from services.integrations.google_docs import GoogleDocsIntegrationService
# Sprint 06 — CRM
from services.integrations.hubspot import HubspotIntegrationService
from services.integrations.salesforce import SalesforceIntegrationService
from services.integrations.pipedrive import PipedriveIntegrationService

logger = logging.getLogger(__name__)


class IntegrationConfigError(RuntimeError):
    """Se lanza cuando faltan credenciales o están malformadas para un provider."""


def _load_provider_config(
    db: Session,
    provider_name: str,
    *,
    tenant_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """Carga la config de un provider con el alcance correcto.

    Per-user (trello/jira/clickup/azure/google/microsoft): busca
    (provider, tenant, user). Sin esa fila → error claro: el usuario
    debe configurar SUS credenciales en /api/settings/me.

    Per-tenant (slack/notion/teams/gdocs/CRM): busca (provider, tenant,
    user_id NULL). El user_id que viene es solo para auditar quién
    disparó la llamada, no para filtrar el alcance."""
    setting = integration_setting_crud.get_by_provider(
        session=db,
        provider_name=provider_name,
        tenant_id=tenant_id,
        user_id=user_id,
    )
    if not setting or not setting.is_active:
        # Mensaje específico para que el frontend pueda discriminar
        # "el admin no la configuró" vs "tú no la configuraste".
        from models import PER_USER_INTEGRATION_PROVIDERS as _PU
        if provider_name in _PU and user_id is not None:
            raise IntegrationConfigError(
                f"Tu cuenta no tiene configurada la integración '{provider_name}'. "
                f"Ve a Configuración → Integraciones para conectarla."
            )
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
    return normalizar_config(config, provider_name)


# La interfaz de Acten guarda las credenciales en camelCase (`apiKey`,
# `apiToken`, `boardId`) y los servicios las leen en snake_case
# (`api_key`, `token`). Nadie lo notó porque el fallo es silencioso: la
# integración aparece «conectada» en pantalla y el despacho revienta con
# «faltan campos obligatorios» dentro de un try. Se normaliza al leer,
# que arregla las filas ya guardadas sin migrar nada.
_ALIAS_POR_PROVIDER = {
    # En Trello, lo que la interfaz llama `apiToken` es el `token`.
    "trello": {"apiToken": "token", "apitoken": "token"},
}


def _camel_a_snake(nombre: str) -> str:
    fuera = []
    for i, ch in enumerate(nombre):
        if ch.isupper() and i:
            fuera.append("_")
        fuera.append(ch.lower())
    return "".join(fuera)


def normalizar_config(config: Dict[str, Any], provider_name: str) -> Dict[str, Any]:
    """Añade la forma snake_case sin tocar lo que ya venga bien.

    No pisa un valor existente con uno vacío: hay filas que arrastran
    ambas formas, la vieja con el valor bueno y la nueva en blanco.
    """
    salida = dict(config)
    alias = _ALIAS_POR_PROVIDER.get(provider_name, {})
    for clave, valor in config.items():
        destino = alias.get(clave) or _camel_a_snake(clave)
        if destino == clave:
            continue
        if str(valor or "").strip() and not str(salida.get(destino) or "").strip():
            salida[destino] = valor
    return salida


def _require(config: Dict[str, Any], keys: list[str], provider: str) -> None:
    missing = [k for k in keys if not config.get(k)]
    if missing:
        raise IntegrationConfigError(
            f"Faltan campos obligatorios en la configuración de '{provider}': "
            f"{', '.join(missing)}."
        )


def get_trello_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> TrelloIntegrationService:
    cfg = _load_provider_config(db, "trello", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["api_key", "token"], "trello")
    return TrelloIntegrationService(cfg["api_key"], cfg["token"])


def get_jira_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> JiraIntegrationService:
    cfg = _load_provider_config(db, "jira", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["domain", "email", "api_token"], "jira")
    return JiraIntegrationService(cfg["domain"], cfg["email"], cfg["api_token"])


def get_clickup_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> ClickUpIntegrationService:
    cfg = _load_provider_config(db, "clickup", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["api_token"], "clickup")
    return ClickUpIntegrationService(cfg["api_token"])


def get_azure_devops_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> AzureDevOpsIntegrationService:
    cfg = _load_provider_config(db, "azure", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["organization", "project", "pat"], "azure")
    return AzureDevOpsIntegrationService(cfg["organization"], cfg["project"], cfg["pat"])


def get_slack_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> SlackIntegrationService:
    cfg = _load_provider_config(db, "slack", tenant_id=tenant_id, user_id=user_id)
    if not cfg.get("webhook_url") and not cfg.get("bot_token"):
        raise IntegrationConfigError("Slack: webhook_url o bot_token requerido.")
    return SlackIntegrationService(
        webhook_url=cfg.get("webhook_url"),
        bot_token=cfg.get("bot_token"),
    )


def get_notion_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> NotionIntegrationService:
    cfg = _load_provider_config(db, "notion", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["integration_token", "database_id"], "notion")
    return NotionIntegrationService(cfg["integration_token"], cfg["database_id"])


def get_msteams_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> MicrosoftTeamsIntegrationService:
    cfg = _load_provider_config(db, "microsoft_teams", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["webhook_url"], "microsoft_teams")
    return MicrosoftTeamsIntegrationService(cfg["webhook_url"])


def get_gdocs_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> GoogleDocsIntegrationService:
    cfg = _load_provider_config(db, "google_docs", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["access_token"], "google_docs")
    return GoogleDocsIntegrationService(cfg["access_token"], cfg.get("refresh_token"))


def get_hubspot_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> HubspotIntegrationService:
    cfg = _load_provider_config(db, "hubspot", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["private_app_token"], "hubspot")
    return HubspotIntegrationService(cfg["private_app_token"])


def get_salesforce_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> SalesforceIntegrationService:
    cfg = _load_provider_config(db, "salesforce", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["instance_url", "access_token"], "salesforce")
    return SalesforceIntegrationService(cfg["instance_url"], cfg["access_token"])


def get_pipedrive_service(
    db: Session, *, tenant_id: Optional[int] = None, user_id: Optional[int] = None,
) -> PipedriveIntegrationService:
    cfg = _load_provider_config(db, "pipedrive", tenant_id=tenant_id, user_id=user_id)
    _require(cfg, ["api_token", "company_domain"], "pipedrive")
    return PipedriveIntegrationService(cfg["api_token"], cfg["company_domain"])


def get_service_for_destination(
    db: Session,
    destination_type: str,
    *,
    tenant_id: Optional[int] = None,
    user_id: Optional[int] = None,
) -> Optional[object]:
    """Devuelve el servicio listo para usar.

    Para providers per-user pasar `user_id` es OBLIGATORIO. Si no
    viene, se intentará cargar la fila per-tenant (que ya no existe para
    Trello/Jira/ClickUp/Azure desde el rollout per-user) → fallará con
    error claro. Para providers per-tenant `user_id` se ignora a la
    hora de filtrar pero se acepta por simetría de la firma.
    """
    dt = (destination_type or "").lower()
    if "trello" in dt: return get_trello_service(db, tenant_id=tenant_id, user_id=user_id)
    if "jira" in dt: return get_jira_service(db, tenant_id=tenant_id, user_id=user_id)
    if "clickup" in dt: return get_clickup_service(db, tenant_id=tenant_id, user_id=user_id)
    if "azure" in dt or "devops" in dt: return get_azure_devops_service(db, tenant_id=tenant_id, user_id=user_id)
    if "slack" in dt: return get_slack_service(db, tenant_id=tenant_id, user_id=user_id)
    if "notion" in dt: return get_notion_service(db, tenant_id=tenant_id, user_id=user_id)
    if "teams" in dt or "msteams" in dt: return get_msteams_service(db, tenant_id=tenant_id, user_id=user_id)
    if "gdocs" in dt or "google_docs" in dt or "googledocs" in dt: return get_gdocs_service(db, tenant_id=tenant_id, user_id=user_id)
    # Sprint 06 — CRM
    if "hubspot" in dt: return get_hubspot_service(db, tenant_id=tenant_id, user_id=user_id)
    if "salesforce" in dt: return get_salesforce_service(db, tenant_id=tenant_id, user_id=user_id)
    if "pipedrive" in dt: return get_pipedrive_service(db, tenant_id=tenant_id, user_id=user_id)
    logger.warning("Destination_type no reconocido: %s", destination_type)
    return None
