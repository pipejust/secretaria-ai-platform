"""Microsoft Teams integration — Adaptive Card via Incoming Webhook (legacy)
o vía Graph API (recomendado).

SCAFFOLDING (Sprint 05). Microsoft está deprecando los Office 365 Connector
webhooks; la implementación real debería usar Graph + Bot Framework.
Para MVP: conector webhook (legacy) que sigue funcionando.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class MicrosoftTeamsIntegrationService:
    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url

    async def post_card(self, *, title: str, summary_md: str, theme_color: str = "4f46e5") -> dict:
        card = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "themeColor": theme_color,
            "title": title,
            "text": summary_md[:1500],
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(self.webhook_url, json=card)
            r.raise_for_status()
            return {"ok": True}
