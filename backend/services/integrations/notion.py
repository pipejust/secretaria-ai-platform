"""Notion integration — crea página en una database.

SCAFFOLDING (Sprint 05). Implementación real con `notion-client`:
- POST /v1/pages con database_id + properties + children.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionIntegrationService:
    def __init__(self, integration_token: str, database_id: str) -> None:
        self.token = integration_token
        self.database_id = database_id
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    async def create_page(self, *, title: str, summary_md: str, project_name: Optional[str] = None) -> dict:
        # Payload mínimo. La estructura real depende del schema del Notion DB del cliente.
        payload = {
            "parent": {"database_id": self.database_id},
            "properties": {
                "Name": {"title": [{"text": {"content": title}}]},
                **({"Project": {"rich_text": [{"text": {"content": project_name}}]}} if project_name else {}),
            },
            "children": [
                {"object": "block", "type": "paragraph",
                 "paragraph": {"rich_text": [{"text": {"content": summary_md[:1900]}}]}},
            ],
        }
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(f"{NOTION_API}/pages", json=payload, headers=self.headers)
            r.raise_for_status()
            return r.json()
