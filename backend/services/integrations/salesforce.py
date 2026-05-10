"""Salesforce CRM integration.

SCAFFOLDING (Sprint 06). REQUIRES_HUMAN_REVIEW: cliente debe configurar
una Connected App + OAuth2 username/password o JWT Bearer flow. Aquí
asumimos que el caller pasa un access_token válido.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class SalesforceIntegrationService:
    def __init__(self, instance_url: str, access_token: str) -> None:
        self.instance_url = instance_url.rstrip("/")
        self.token = access_token
        self.headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    async def find_opportunity_by_email(self, email: str) -> Optional[dict]:
        # SOQL contra Lead/Contact + Opportunity
        soql = f"SELECT Id,Name FROM Opportunity WHERE Account.Owner.Email='{email}' LIMIT 1"
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(f"{self.instance_url}/services/data/v60.0/query",
                            params={"q": soql}, headers=self.headers)
            r.raise_for_status()
            recs = r.json().get("records", [])
            return recs[0] if recs else None

    async def attach_task(self, what_id: str, subject: str, description: str) -> dict:
        body = {"WhatId": what_id, "Subject": subject[:255],
                "Description": description[:32000], "Status": "Completed"}
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"{self.instance_url}/services/data/v60.0/sobjects/Task",
                             json=body, headers=self.headers)
            r.raise_for_status()
            return r.json()
