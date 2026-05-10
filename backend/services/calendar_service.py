"""Calendar integration — Google Calendar + Microsoft Graph.

SCAFFOLDING (Sprint 03). La implementación real requiere:
- Configurar OAuth2 apps en Google Cloud y Azure AD
- Tabla calendar_account con tokens cifrados (AES-GCM, KEK desde env)
- Cron sync 30 min en services/cron_service.py
- Endpoints OAuth callback en routers/

Marcado requires_human_review en architect.output.json: el operador debe
crear las apps OAuth y aportar client_id/secret antes del primer deploy.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GoogleCalendarService:
    """Stub. La implementación real necesita google-auth-oauthlib + google-api-python-client."""

    def __init__(self, access_token: str, refresh_token: Optional[str] = None) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token

    async def list_upcoming_events(self, days: int = 7) -> list[dict]:
        # TODO Sprint 03: GET /calendar/v3/calendars/primary/events?timeMin=now&timeMax=now+days
        logger.info("[STUB] GoogleCalendarService.list_upcoming_events days=%s", days)
        return []


class MicrosoftCalendarService:
    """Stub. Implementación real necesita msal + Microsoft Graph SDK."""

    def __init__(self, access_token: str, refresh_token: Optional[str] = None) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token

    async def list_upcoming_events(self, days: int = 7) -> list[dict]:
        # TODO Sprint 03: GET /me/calendarView?startDateTime=now&endDateTime=now+days
        logger.info("[STUB] MicrosoftCalendarService.list_upcoming_events days=%s", days)
        return []
