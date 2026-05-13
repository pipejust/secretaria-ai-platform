import json
import logging
from typing import Any, Dict, Optional

import httpx
from sqlmodel import Session, select

from config import settings
from models import IntegrationSetting

logger = logging.getLogger(__name__)


def get_fireflies_api_key(db: Session, tenant_id: int) -> Optional[str]:
    """Resuelve la API key de Fireflies del tenant.

    Orden de precedencia:
      1. `IntegrationSetting(provider_name='fireflies', tenant_id=…).config_json.apiKey`
         (el campo que la UI admin guarda — fuente de verdad multi-tenant).
      2. Env var `FIREFLIES_API_KEY` (fallback para dev local / testing).

    Devuelve `None` si ninguna fuente la tiene — el llamador debe decidir si
    aborta o sigue con un error explícito.
    """
    row = db.exec(
        select(IntegrationSetting)
        .where(IntegrationSetting.provider_name == "fireflies")
        .where(IntegrationSetting.tenant_id == tenant_id)
    ).first()
    if row and row.config_json:
        try:
            cfg = json.loads(row.config_json)
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        # El frontend guarda el campo como `apiKey` (camelCase).
        # Aceptamos también `api_key` por si algún seed/script vino por otro lado.
        api_key = (cfg.get("apiKey") or cfg.get("api_key") or "").strip()
        if api_key:
            return api_key
    # Fallback de env var (compat dev).
    env_key = (settings.fireflies_api_key or "").strip()
    return env_key or None


class FirefliesService:
    """Cliente Fireflies GraphQL.

    Multi-tenant: la API key se pasa por constructor. Cada llamador (router,
    script de re-ingesta, etc.) DEBE resolverla con `get_fireflies_api_key`
    antes de construir el servicio. Si no hay key, instanciar igual no rompe
    pero la primera request va a recibir 401.
    """

    BASE_URL = "https://api.fireflies.ai/graphql"

    def __init__(self, api_key: Optional[str] = None) -> None:
        # Compat: si no se pasa, cae a env var (no toca DB porque acá no hay
        # session SQL — esto preserva el comportamiento legacy en dev y tests).
        self.api_key: str = (api_key or settings.fireflies_api_key or "").strip()
        if not self.api_key:
            logger.warning(
                "FirefliesService instanciado sin API key (ni por argumento ni env). "
                "Las requests a Fireflies van a fallar con 401."
            )

    async def get_transcript_data(self, transcript_id: str) -> Dict[str, Any]:
        """Consulta la API de Fireflies para obtener datos detallados de una reunión."""
        query = """
        query MeetingRichOutput($transcriptId: String!) {
            transcript(id: $transcriptId) {
                id
                title
                dateString
                duration
                summary {
                    overview
                    short_summary
                    notes
                    action_items
                    topics_discussed
                    keywords
                    outline
                    bullet_gist
                }
                sentences {
                    text
                    speaker_name
                }
                analytics {
                    sentiments {
                        positive_pct
                        neutral_pct
                        negative_pct
                    }
                    speakers {
                        speaker_id
                        name
                        duration
                        word_count
                    }
                }
            }
            apps(transcript_id: $transcriptId, limit: 10) {
                outputs {
                    title
                    response
                    created_at
                }
            }
        }
        """
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "query": query,
            "variables": {"transcriptId": transcript_id}
        }

        async with httpx.AsyncClient(timeout=None) as client:
            try:
                response = await client.post(self.BASE_URL, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.ReadTimeout:
                print(f"Fireflies API ReadTimeout fetching transcript {transcript_id}")
                raise
            except Exception as e:
                import traceback
                print(f"Fireflies API Error fetching transcript:")
                print(traceback.format_exc())
                raise
                
            data = response.json()
            
            # Manejo de errores de GraphQL
            if "errors" in data:
                # Retornamos el error para que la ruta pueda atraparlo si es object_not_found
                raise Exception(data["errors"])
                
            result = data["data"].get("transcript") or {}
            result["apps_layer"] = data["data"].get("apps", {})
            return result
