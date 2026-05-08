"""Servicio LLM contra Groq Cloud (Llama 3.3 70B).

Se usa para todo el procesamiento automático que hoy disparaba el webhook
de Fireflies, EXCEPTO la extracción de tareas (action_items), que se mantiene
en OpenAI por su superioridad en JSON Schema estricto.

Reglas de uso:
- Siempre devolver JSON con `response_format={"type": "json_object"}`.
- El system prompt fuerza español.
- Un solo round-trip para evitar rate limits (Groq es muy rápido pero el
  free tier es estricto en RPM).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)


class GroqLLMService:
    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.3-70b-versatile"   # 128k context, 32k output, JSON mode

    def __init__(self) -> None:
        if not settings.groq_api_key:
            logger.warning(
                "GROQ_API_KEY no configurada. GroqLLMService devolverá vacíos."
            )
        self.headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def process_fundamentals_and_insights(
        self,
        transcript: str,
        project_contacts: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Extrae todo lo no-tareas: idioma, asistentes, temas, decisiones, riesgos, acuerdos.

        Devuelve dict con claves:
            language: str
            attendees: list[{name, role, entity}]
            themes: list[{theme_name, discussion_points: list[str]}]
            decisions: str (texto largo)
            risks: str (texto largo)
            agreements: str (texto largo)
        """
        if not transcript or not transcript.strip():
            return self._empty_payload()

        if not settings.groq_api_key:
            return self._empty_payload()

        contacts_block = ""
        if project_contacts:
            contacts_block = (
                "\nLista de personas conocidas del proyecto (úsala solo para enriquecer "
                "datos, no para limitar la extracción):\n"
                f"{json.dumps(project_contacts, ensure_ascii=False)}\n"
            )

        current_date = datetime.now().strftime("%Y-%m-%d")
        system_prompt = (
            "Eres un asistente experto que analiza transcripciones de reuniones "
            f"corporativas. La fecha actual es {current_date}. "
            "REGLA DE ORO: tu respuesta debe estar EXCLUSIVAMENTE EN ESPAÑOL, "
            "sin importar el idioma original de la reunión. Devuelves SIEMPRE "
            "un único objeto JSON válido siguiendo el esquema solicitado."
        )

        user_prompt = f"""
Analiza la siguiente transcripción y devuelve un JSON con EXACTAMENTE estas claves:

{{
  "language": "idioma original detectado (Español, Inglés, Portugués, etc.)",
  "attendees": [
    {{ "name": "Nombre Completo", "role": "Cargo o rol", "entity": "Empresa o entidad" }}
  ],
  "themes": [
    {{ "theme_name": "Título del tema", "discussion_points": ["punto 1", "punto 2"] }}
  ],
  "decisions": "TEXTO MUY EXTENSO (3 a 5 párrafos grandes en prosa, SIN viñetas) explicando cada decisión clave, su motivación y contexto.",
  "risks": "TEXTO MUY EXTENSO (3 a 5 párrafos en prosa, SIN viñetas) describiendo bloqueos, preocupaciones y riesgos detectados, con su gravedad.",
  "agreements": "TEXTO MUY EXTENSO (3 a 5 párrafos en prosa, SIN viñetas) detallando acuerdos generales, consensos y metodologías acordadas."
}}

Reglas estrictas:
- attendees: extrae a TODOS los participantes mencionados, incluso si no están en la lista de contactos.
- themes: usa títulos cortos y descriptivos, con sub-puntos detallados.
- decisions / risks / agreements: prosa larga y rica en contexto. Si la reunión no toca alguno, usa una frase breve indicándolo.
- Nunca inventes contenido que no esté en la transcripción.
{contacts_block}
Transcripción:
{transcript}
""".strip()

        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        return await self._post_with_retries(payload, fallback=self._empty_payload())

    async def deduce_project(
        self, summary_or_transcript: str, projects: List[Dict[str, Any]]
    ) -> Optional[int]:
        """Decide a qué proyecto pertenece la reunión. Devuelve project_id o None."""
        if not projects or not summary_or_transcript:
            return None
        if not settings.groq_api_key:
            return None

        snippet = summary_or_transcript[:6000]
        prompt = (
            "Eres un clasificador estricto. Dado el siguiente texto de una reunión "
            "y la lista de proyectos disponibles (id, name, description), decide "
            "a cuál proyecto pertenece. Responde EXCLUSIVAMENTE con un JSON "
            '{"project_id": <int>} o {"project_id": null} si ninguno encaja.\n\n'
            f"Texto:\n{snippet}\n\nProyectos:\n"
            f"{json.dumps(projects, ensure_ascii=False)}"
        )

        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": 'Responde solo {"project_id": int|null}.'},
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0,
        }

        result = await self._post_with_retries(payload, fallback={"project_id": None})
        pid = result.get("project_id")
        return int(pid) if isinstance(pid, (int, str)) and str(pid).isdigit() else None

    async def clean_native_summary(self, dirty_summary: str) -> str:
        """Limpia el summary nativo de Fireflies (traduce headers al español, quita asteriscos).

        Si Groq no está disponible o falla, devuelve el original sin tocar.
        """
        if not dirty_summary or not dirty_summary.strip():
            return ""
        if not settings.groq_api_key:
            return dirty_summary

        prompt = (
            "Eres un editor de actas corporativas. Te paso un resumen generado por "
            "otra IA (Fireflies) que puede traer encabezados en inglés (TOPICS, "
            "BLOCKERS, etc.), referencias `**[Fuente: ...]**` y asteriscos markdown. "
            "Devuelve el MISMO contenido pero:\n"
            "1) Traduce todos los encabezados al español ('Temas Principales', "
            "'Bloqueos y Retrasos', etc.).\n"
            "2) Elimina referencias literales tipo `**[Fuente: nombre, fecha]**`.\n"
            "3) Elimina asteriscos markdown (`**`).\n"
            "4) Elimina títulos introductorios redundantes tipo 'Daily Digest "
            "Resumen Ejecutivo Consolidado — ProyectoX'.\n"
            "5) Respeta hechos, responsables, fechas y puntos clave: solo pules forma.\n"
            "Responde EXCLUSIVAMENTE con el texto limpio, sin explicaciones tuyas.\n\n"
            f"Texto original:\n{dirty_summary}"
        )
        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await self._post(client, payload)
                return response["choices"][0]["message"]["content"].strip()
            except Exception:
                logger.exception("Error limpiando summary nativo con Groq")
                return dirty_summary

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _empty_payload() -> Dict[str, Any]:
        return {
            "language": "Español",
            "attendees": [],
            "themes": [],
            "decisions": "",
            "risks": "",
            "agreements": "",
        }

    async def _post(
        self, client: httpx.AsyncClient, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
        response.raise_for_status()
        return response.json()

    async def _post_with_retries(
        self,
        payload: Dict[str, Any],
        fallback: Dict[str, Any],
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=120.0) as client:
            for attempt in range(max_attempts):
                try:
                    response = await client.post(
                        self.BASE_URL, json=payload, headers=self.headers
                    )
                    if response.status_code == 429 and attempt < max_attempts - 1:
                        wait_seconds = self._parse_retry_after(response.text, attempt)
                        logger.info(
                            "Groq 429: durmiendo %ss antes de reintentar (intento %s)",
                            wait_seconds,
                            attempt + 1,
                        )
                        await asyncio.sleep(wait_seconds)
                        continue
                    response.raise_for_status()
                    raw = response.json()["choices"][0]["message"]["content"]
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict):
                        return fallback
                    return parsed
                except httpx.HTTPStatusError as exc:
                    logger.warning(
                        "Groq HTTP %s: %s",
                        exc.response.status_code,
                        exc.response.text[:300],
                    )
                    if attempt == max_attempts - 1:
                        return fallback
                except (json.JSONDecodeError, KeyError, IndexError):
                    logger.exception("Groq devolvió JSON inválido")
                    return fallback
                except Exception:
                    logger.exception("Error inesperado llamando a Groq")
                    if attempt == max_attempts - 1:
                        return fallback
        return fallback

    @staticmethod
    def _parse_retry_after(body: str, attempt: int) -> float:
        default = 2.0 + attempt * 2
        try:
            match = re.search(r"try again in (\d+\.?\d*)s", body)
            if match:
                return float(match.group(1)) + 1.0
        except (AttributeError, ValueError, TypeError):
            pass
        return default
