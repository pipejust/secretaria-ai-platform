"""Slack integration — Incoming Webhook (simple) o Bot OAuth (avanzado).

SCAFFOLDING (Sprint 05). Implementación real:
- Para webhook: POST a la URL configurada en IntegrationSetting('slack').config_json.webhook_url
  con payload Slack message format (text + blocks).
- Para Bot OAuth: usar slack_sdk + chat.postMessage.

Aquí solo dejamos la firma. El ServiceFactory en
backend/services/integrations/__init__.py la incorpora cuando exista
config válida.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class SlackIntegrationService:
    def __init__(self, webhook_url: Optional[str] = None, bot_token: Optional[str] = None) -> None:
        if not webhook_url and not bot_token:
            raise ValueError("Slack requiere webhook_url o bot_token.")
        self.webhook_url = webhook_url
        self.bot_token = bot_token

    async def post_summary(self, *, channel: Optional[str], title: str, summary_md: str, action_items_count: int) -> dict:
        text = f"*{title}*\n{summary_md[:1500]}\n\n_{action_items_count} tareas_"
        if self.webhook_url:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.post(self.webhook_url, json={"text": text})
                r.raise_for_status()
                return {"ok": True, "via": "webhook"}
        # TODO Sprint 05: Bot OAuth con chat.postMessage
        logger.info("[STUB] SlackIntegrationService bot_token branch — channel=%s", channel)
        return {"ok": False, "via": "bot", "stub": True}
