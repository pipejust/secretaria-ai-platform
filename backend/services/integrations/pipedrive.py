"""Pipedrive CRM integration.

SCAFFOLDING (Sprint 06). API token simple desde Settings → Personal preferences.
"""

from __future__ import annotations

from typing import Optional

import httpx


class PipedriveIntegrationService:
    def __init__(self, api_token: str, company_domain: str) -> None:
        self.token = api_token
        self.base = f"https://{company_domain}.pipedrive.com/api/v1"

    async def find_deal_by_email(self, email: str) -> Optional[dict]:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(f"{self.base}/persons/search",
                            params={"term": email, "fields": "email", "api_token": self.token})
            r.raise_for_status()
            items = r.json().get("data", {}).get("items", [])
            if not items:
                return None
            person_id = items[0]["item"]["id"]
            r2 = await c.get(f"{self.base}/persons/{person_id}/deals",
                             params={"api_token": self.token, "status": "open"})
            r2.raise_for_status()
            deals = r2.json().get("data") or []
            return deals[0] if deals else None

    async def add_note(self, deal_id: int, content_html: str) -> dict:
        body = {"content": content_html[:32000], "deal_id": deal_id}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{self.base}/notes", json=body,
                             params={"api_token": self.token})
            r.raise_for_status()
            return r.json()
