"""Google Docs integration — crea un Doc nuevo con el acta.

SCAFFOLDING (Sprint 05). Implementación real:
- Reusar credenciales OAuth de Sprint 03 (Google Calendar) ampliadas con
  scope https://www.googleapis.com/auth/documents.
- POST /v1/documents (Docs API) → batchUpdate para insertar contenido.
- Opcional: mover a una carpeta del Drive del usuario.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GoogleDocsIntegrationService:
    def __init__(self, access_token: str, refresh_token: Optional[str] = None) -> None:
        self.access_token = access_token
        self.refresh_token = refresh_token

    async def create_doc(self, *, title: str, body_md: str, folder_id: Optional[str] = None) -> dict:
        # TODO Sprint 05: usar google-api-python-client
        logger.info("[STUB] GoogleDocsIntegrationService.create_doc title=%s", title)
        return {"stub": True, "doc_id": None, "url": None}
