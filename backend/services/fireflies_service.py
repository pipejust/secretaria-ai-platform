import asyncio
import json
import logging
from typing import Any, Dict, Optional

import httpx
from sqlmodel import Session, select

from config import settings
from models import IntegrationSetting, MeetingSession

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

    # Query "core" — los campos que SI usamos en el pipeline (transcript,
    # summary, sentences). Compatible con Fireflies free tier.
    _QUERY_CORE = """
    query MeetingCore($transcriptId: String!) {
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

    async def get_transcript_data(self, transcript_id: str) -> Dict[str, Any]:
        """Consulta la API de Fireflies para obtener datos de una reunión.

        Solo pide los campos que el pipeline IA realmente consume:
        title, dateString, duration, summary, sentences, apps.

        ANTES pedíamos también `analytics { sentiments, speakers }` pero ese
        campo es PAGO en Fireflies — devuelve `paid_required` y rompe la
        query entera para usuarios free tier. Como nunca usamos analytics
        en el resto del código, lo removí del query.
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": self._QUERY_CORE,
            "variables": {"transcriptId": transcript_id},
        }

        async with httpx.AsyncClient(timeout=None) as client:
            try:
                response = await client.post(self.BASE_URL, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.ReadTimeout:
                logger.warning("Fireflies ReadTimeout fetching transcript %s", transcript_id)
                raise
            except Exception:
                import traceback
                logger.error("Fireflies API HTTP error: %s", traceback.format_exc())
                raise

            data = response.json()

            # Manejo de errores de GraphQL.
            if "errors" in data:
                errors = data["errors"]
                # Caso especial: si el ÚNICO error es paid_required en algún
                # campo opcional y `transcript` igual vino con datos, los
                # devolvemos. Fireflies a veces devuelve datos parciales +
                # errors[].
                transcript_data = (data.get("data") or {}).get("transcript")
                if transcript_data and self._all_errors_are_paid_required(errors):
                    logger.info(
                        "Fireflies devolvió datos parciales para %s (algunos "
                        "campos paid-only ignorados).",
                        transcript_id,
                    )
                    transcript_data["apps_layer"] = (data.get("data") or {}).get("apps", {})
                    return transcript_data
                # Si no hay datos utilizables, propagamos el error.
                raise Exception(errors)

            result = data["data"].get("transcript") or {}
            result["apps_layer"] = data["data"].get("apps", {})
            return result

    @staticmethod
    def _all_errors_are_paid_required(errors: list) -> bool:
        """True si TODOS los errores devueltos son por feature paga.
        Permite degradar gracefully: si solo se cae el campo paid-only,
        igual devolvemos los datos free-tier que sí trajeron."""
        if not errors:
            return False
        for e in errors:
            ext = (e.get("extensions") or {}) if isinstance(e, dict) else {}
            code = ext.get("code") or e.get("code") if isinstance(e, dict) else ""
            if code != "paid_required":
                return False
        return True


# ---------------------------------------------------------------------------
# Helpers para re-fetch de campos específicos (no toda la sesión).
# ---------------------------------------------------------------------------


def _extract_summary_text(summary_obj: Any) -> str:
    """Compone texto del resumen ejecutivo nativo de Fireflies.

    Duplica `_extract_native_summary` de routers/fireflies.py para que esta
    función sea autocontenida (no importa el router para evitar circulares).
    Fireflies devuelve summary como dict con campos opcionales — usamos los
    que estén poblados.
    """
    if not isinstance(summary_obj, dict):
        return str(summary_obj or "").strip()

    parts: list[str] = []
    overview = (summary_obj.get("overview") or "").strip()
    if overview:
        parts.append(f"### Resumen General\n{overview}")
    bullet_gist = (summary_obj.get("bullet_gist") or "").strip()
    if bullet_gist:
        parts.append(f"### Puntos Clave\n{bullet_gist}")
    notes = (summary_obj.get("notes") or "").strip()
    if notes:
        parts.append(f"### Notas\n{notes}")
    short_summary = (summary_obj.get("short_summary") or "").strip()
    if short_summary and not parts:
        parts.append(f"### Resumen\n{short_summary}")
    return "\n\n".join(parts).strip()


async def fetch_native_summary(
    fireflies_id: str,
    *,
    api_key: Optional[str] = None,
    max_attempts: int = 3,
    backoff_base_sec: float = 5.0,
) -> str:
    """Pide a Fireflies SOLO el campo summary de un transcript.

    Reintenta hasta `max_attempts` veces con backoff exponencial cuando el
    summary llega vacío — esto cubre el caso típico donde el webhook se dispara
    antes de que Fireflies haya terminado de generar el resumen ejecutivo
    (que es asincrónico de su lado, normalmente listo en 30 s a 2 min).

    Devuelve el resumen formateado como markdown listo para guardar en
    `MeetingSession.raw_summary`. Si tras todos los intentos sigue vacío,
    devuelve "" — el caller decide si re-encolar o avisar al admin.
    """
    if not fireflies_id:
        return ""

    service = FirefliesService(api_key=api_key)

    for attempt in range(1, max_attempts + 1):
        try:
            ff_data = await service.get_transcript_data(fireflies_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "fetch_native_summary: error en intento %s/%s para %s: %s",
                attempt, max_attempts, fireflies_id, exc,
            )
            ff_data = {}

        summary_text = _extract_summary_text(ff_data.get("summary"))
        if summary_text:
            logger.info(
                "fetch_native_summary: summary obtenido para %s en intento %s "
                "(%s chars).",
                fireflies_id, attempt, len(summary_text),
            )
            return summary_text

        if attempt < max_attempts:
            wait = backoff_base_sec * (2 ** (attempt - 1))
            logger.info(
                "fetch_native_summary: summary vacío para %s (intento %s/%s). "
                "Esperando %.1fs y reintentando.",
                fireflies_id, attempt, max_attempts, wait,
            )
            await asyncio.sleep(wait)

    logger.warning(
        "fetch_native_summary: summary sigue vacío para %s tras %s intentos. "
        "Probablemente Fireflies aún no lo generó del lado de ellos.",
        fireflies_id, max_attempts,
    )
    return ""


async def refetch_summary_for_session(
    db: Session,
    session: MeetingSession,
    *,
    api_key: Optional[str] = None,
    clean_with_groq: bool = True,
    max_attempts: int = 3,
) -> bool:
    """Re-baja el summary nativo desde Fireflies y lo guarda en la sesión.

    Devuelve True si pudo escribir un summary no vacío, False si Fireflies
    no tenía nada que devolver (o falló).

    Si `clean_with_groq=True`, pasa el texto crudo por Groq para limpiar
    headers, asteriscos y referencias `[Fuente: ...]` antes de persistir.
    Si Groq falla, persiste el texto crudo (mejor mostrar algo que nada).
    """
    if not session.fireflies_id:
        return False

    summary = await fetch_native_summary(
        session.fireflies_id,
        api_key=api_key,
        max_attempts=max_attempts,
    )
    if not summary:
        return False

    if clean_with_groq:
        try:
            from services.llm_groq import GroqLLMService
            groq = GroqLLMService()
            cleaned = await groq.clean_native_summary(summary)
            if cleaned and cleaned.strip():
                summary = cleaned
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "refetch_summary_for_session: Groq cleanup falló para sesión %s "
                "(se guarda crudo): %s",
                session.id, exc,
            )

    session.raw_summary = summary
    db.add(session)
    db.commit()
    logger.info(
        "refetch_summary_for_session: sesión %s actualizada con summary "
        "de %s chars desde Fireflies %s.",
        session.id, len(summary), session.fireflies_id,
    )
    return True
