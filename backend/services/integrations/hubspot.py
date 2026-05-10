"""HubSpot CRM integration — find_deal_by_email + attach_note.

SCAFFOLDING (Sprint 06). REQUIRES_HUMAN_REVIEW: cliente debe aportar
private app token con permisos `crm.objects.deals.read` + `crm.objects.notes.write`.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)
HS_API = "https://api.hubapi.com"


class HubspotIntegrationService:
    def __init__(self, private_app_token: str) -> None:
        self.token = private_app_token
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def find_deal_by_email(self, email: str) -> Optional[dict]:
        async with httpx.AsyncClient(timeout=15) as c:
            # 1. Encuentra el contact_id por email
            r = await c.get(f"{HS_API}/crm/v3/objects/contacts/{email}",
                            params={"idProperty": "email"}, headers=self.headers)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            contact_id = r.json().get("id")
            # 2. Busca deals asociados
            r2 = await c.get(f"{HS_API}/crm/v4/objects/contacts/{contact_id}/associations/deals",
                             headers=self.headers)
            r2.raise_for_status()
            deals = r2.json().get("results", [])
            return deals[0] if deals else None

    async def attach_note(self, deal_id: str, html_content: str) -> dict:
        # Crea una engagement de tipo NOTE asociada al deal
        body = {
            "properties": {"hs_note_body": html_content[:65535],
                           "hs_timestamp": __import__("time").time() * 1000},
            "associations": [{
                "to": {"id": deal_id},
                "types": [{"associationCategory": "HUBSPOT_DEFINED", "associationTypeId": 214}],
            }],
        }
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{HS_API}/crm/v3/objects/notes", json=body, headers=self.headers)
            r.raise_for_status()
            return r.json()
