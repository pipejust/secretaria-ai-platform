import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from config import settings

import services.groq_models as _modelos

logger = logging.getLogger(__name__)


class OpenAIService:
    """
    Servicio LLM. Históricamente se llamó `GroqService`; el nombre se conserva
    como alias al final del módulo por compatibilidad de imports, pero la
    implementación real apunta a OpenAI (gpt-4o / gpt-4o-mini / Whisper).
    """

    BASE_URL = "https://api.openai.com/v1/chat/completions"
    MODEL = "gpt-4o"  # Alta fidelidad en extracción JSON
    
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {settings.openai_api_key}",
            "Content-Type": "application/json"
        }
        
    async def transcribe_audio(self, file_bytes: bytes, filename: str) -> str:
        """Transcribe an audio file using OpenAI's Whisper."""
        url = "https://api.openai.com/v1/audio/transcriptions"
        
        # Determine language or default to multilingüe for Whisper
        # We use a Multipart form data request
        files = {
            "file": (filename, file_bytes, "audio/mpeg")
        }
        data = {
            "model": "whisper-1",
            "response_format": "json"
        }
        
        headers = {
            "Authorization": f"Bearer {settings.openai_api_key}"
            # Do NOT set Content-Type to application/json, httpx will set multipart/form-data automatically
        }
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                response = None
                for attempt in range(3):
                    response = await client.post(url, files=files, data=data, headers=headers)
                    if response.status_code == 429 and attempt < 2:
                        wait_seconds = 2 + attempt * 2
                        try:
                            match = re.search(r'try again in (\d+\.?\d*)s', response.text)
                            if match:
                                wait_seconds = float(match.group(1)) + 1.0
                        except (AttributeError, ValueError, TypeError) as parse_err:
                            logger.debug("No se pudo parsear retry-after: %s", parse_err)
                        logger.info("Durmiendo %ss antes de reintentar transcripción...", wait_seconds)
                        await asyncio.sleep(wait_seconds)
                        continue
                    response.raise_for_status()
                    break
                return response.json().get("text", "")
            except Exception as e:
                logger.exception("Error transcribiendo audio")
                raise RuntimeError(f"Fallo en la transcripción de audio: {e}") from e
        
    def _get_fundamentals_schema(self) -> Dict[str, Any]:
        """Schema para Agent 1: Resumen y metadatos"""
        return {
            "type": "object",
            "properties": {
                "language": {"type": "string", "description": "El idioma original detectado de la transcripción (ej: Inglés, Español, Portugués)"},
                "summary": {"type": "string", "description": "Un resumen extenso, minucioso y muy detallado de toda la reunión."},
                "attendees": {
                    "type": "array",
                    "description": "Lista de participantes de la reunión, extrayendo nombre, cargo y entidad/empresa si se mencionan.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "role": {"type": "string", "description": "Cargo o rol del participante"},
                            "entity": {"type": "string", "description": "Empresa o entidad a la que pertenece"}
                        },
                        "required": ["name", "role", "entity"]
                    }
                },
                "themes": {
                    "type": "array",
                    "description": "Lista de temas principales discutidos en la reunión, con sus respectivos puntos de discusión.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "theme_name": {"type": "string", "description": "Título corto del tema discutido, ej. 'Revisión servidor y WAR'"},
                            "discussion_points": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Lista de los detalles o puntos principales hablados en este tema específico"
                            }
                        },
                        "required": ["theme_name", "discussion_points"]
                    }
                }
            },
            "required": ["language", "summary", "attendees", "themes"]
        }

    def _get_insights_schema(self) -> Dict[str, Any]:
        """Schema para Agent 2: Decisiones / Riesgos / Acuerdos en formato viñetado.

        Cambio importante: el lector tiene 30 segundos para entender el acta.
        Forzamos viñetas Markdown con responsable explícito por línea, no
        paredes de texto.
        """
        return {
            "type": "object",
            "properties": {
                "decisions": {
                    "type": "string",
                    "description": (
                        "Lista Markdown. UNA viñeta `-` por decisión. "
                        "Formato exacto por línea:\n"
                        "- **[Tema]** Decisión concreta — Responsable: <Nombre o 'por definir'>\n"
                        "Si necesitas contexto, segunda línea sangrada con `  · contexto: <una frase>`. "
                        "NO uses párrafos largos."
                    ),
                },
                "risks": {
                    "type": "string",
                    "description": (
                        "Lista Markdown. UNA viñeta `-` por riesgo. Formato:\n"
                        "- **[Severidad: alta/media/baja]** Riesgo concreto — Responsable de seguimiento: <Nombre>\n"
                        "Si la reunión no menciona riesgos, devuelve `- Sin riesgos identificados.`"
                    ),
                },
                "agreements": {
                    "type": "string",
                    "description": (
                        "Lista Markdown. UNA viñeta `-` por acuerdo. Formato:\n"
                        "- **[Tema]** Acuerdo concreto — Partes: <Nombres>\n"
                        "NO uses párrafos largos."
                    ),
                },
            },
            "required": ["decisions", "risks", "agreements"],
        }

    def _get_tasks_only_json_schema(self) -> Dict[str, Any]:
        """Define la estructura estricta enfocada exclusivamente en tareas para no diluir el contexto de la IA"""
        return {
            "type": "object",
            "properties": {
                "thinking_process": {
                    "type": "string",
                    "description": "PASO 1: ANÁLISIS EXHAUSTIVO. Procesa la reunión y asimila que DEBES extraer LAS TAREAS EXACTAS que haya en la transcripción, sin importar cuántas sean. Ni inventes, ni omitas."
                },
                "action_items": {
                    "type": "array",
                    "description": "PASO 2: Lista de tareas. Extrae EXACTAMENTE las tareas que estén en la transcripción, desglosando al máximo nivel de detalle para no agrupar iniciativas, igual que lo haría GPT-4. No tienes un mínimo ni un máximo, guíate al 100% por la realidad del texto.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "owner_name": {"type": "string"},
                            "owner_email": {"type": "string"},
                            "title": {"type": "string", "description": "Título claro y descriptivo de la tarea específica."},
                            "description": {"type": "string", "description": "Usa ESTRICTAMENTE el siguiente formato separador con saltos de línea y texto para estructurar esta tarea específica:\nObjetivo: [texto]\nDetalle específico: [texto]\nActividades puntuales: [texto]\nEntregable: [texto]\nCriterio de cierre: [texto]"},
                            "due_date": {"type": "string", "description": "Revisa tu thinking_process para colocar la fecha o día exacto acordado en formato YYYY-MM-DD."},
                            "due_time": {"type": "string", "description": "Hora exacta del compromiso en formato HH:MM (24h). Vacío si la reunión no la fijó."},
                            "priority": {"type": "string", "enum": ["alta", "media", "baja"], "description": "Prioridad inferida: 'alta' si vence pronto/es bloqueante/se mencionó como urgente, 'baja' si es a futuro o nice-to-have, 'media' por defecto."}
                        },
                        "required": ["owner_name", "owner_email", "title", "description", "due_date", "due_time", "priority"]
                    }
                }
            },
            "required": ["thinking_process", "action_items"]
        }

    # Mapeo i18n: código → nombre del idioma para el prompt del LLM.
    _LANG_NAMES_TASKS = {"es": "ESPAÑOL", "ca": "CATALÁN", "en": "INGLÉS"}

    @classmethod
    def _resolve_lang_name(cls, lang):
        return cls._LANG_NAMES_TASKS.get((lang or "es").lower(), "ESPAÑOL")

    async def process_transcript_for_tasks_only(
        self,
        transcript: str,
        project_contacts: list = None,
        decisions: str = "",
        agreements: str = "",
        summary: str = "",
        output_language: str = "es",
    ) -> dict:
        """
        Envía el transcript a Groq pidiendo EXCLUSIVAMENTE action_items.

        IMPORTANTE: además del transcript, ahora pasamos las secciones ya
        procesadas (decisiones, acuerdos, resumen). Esto resuelve un bug
        observado en producción donde compromisos claramente acordados
        ("se desarrollará un manual de marca", "se realizará una validación")
        aparecían en la sección de Acuerdos pero NO en las tareas
        generadas — porque el LLM solo veía el transcript ruidoso y no
        las secciones ya curadas con los compromisos limpios.

        Estrategia: el LLM extrae primero del transcript, después VERIFICA
        contra los acuerdos y decisiones, y agrega cualquier compromiso
        que no haya capturado. La cobertura sube significativamente.
        """
        safe_transcript = transcript # REMOVED TRUNCATION, GPT-4o handles 128k context

        contacts_info = ""
        if project_contacts:
            contacts_str = json.dumps(project_contacts, ensure_ascii=False)
            contacts_info = f"\n\nTienes acceso a la siguiente lista de personas del proyecto:\n{contacts_str}\nSi una tarea es asignada a una persona de esta lista, debes usar su 'name' y 'email' exactos.\n"

        current_date = datetime.now().strftime("%Y-%m-%d")

        # Bloques opcionales con secciones ya procesadas. Cada uno se
        # incluye sólo si tiene contenido — evita confundir al LLM con
        # placeholders vacíos.
        extra_context_blocks = []
        if (summary or "").strip():
            extra_context_blocks.append(
                f"=== RESUMEN EJECUTIVO YA PROCESADO ===\n{summary.strip()}"
            )
        if (decisions or "").strip():
            extra_context_blocks.append(
                f"=== DECISIONES CLAVE YA PROCESADAS ===\n{decisions.strip()}"
            )
        if (agreements or "").strip():
            extra_context_blocks.append(
                f"=== ACUERDOS YA PROCESADOS (FUENTE PRIMARIA DE TAREAS) ===\n"
                f"{agreements.strip()}"
            )
        extra_context = (
            "\n\n" + "\n\n".join(extra_context_blocks)
            if extra_context_blocks
            else ""
        )

        _lang_name = self._resolve_lang_name(output_language)
        prompt = f"""
        Eres un asistente experto que procesa transcripciones de reuniones internacionales.
        Tu ÚNICO OBJETIVO es extraer los compromisos y tareas con el MÁXIMO detalle posible.

        ¡MUY IMPORTANTE - REGLA DE ORO!: SIN IMPORTAR EL IDIOMA DE LA TRANSCRIPCIÓN, LAS TAREAS DEBEN SER GENERADAS EXCLUSIVAMENTE Y ESTRICTAMENTE EN {_lang_name}.

        DATO CLAVE DE CONTEXTO TEMPORAL:
        La fecha actual es {current_date}. Utiliza esta información para inferir correctamente los años y fechas relativas (ej. si dicen "el próximo martes" o "para el 15 de marzo", usa el año actual o el correspondiente). NUNCA asumas años pasados si no se dicen explícitamente.

        PRECAUCIÓN MUY IMPORTANTE SOBRE BÚSQUEDA DE CORREOS:
        Intenta identificar y extraer los correos electrónicos mencionados para asignarlos a 'owner_email'. {contacts_info}

        INSTRUCCIONES CLAVE PARA TAREAS (ACTION ITEMS) - FIDELIDAD ABSOLUTA Y COBERTURA TOTAL:
        Eres un analista implacable. Tu objetivo es la FIDELIDAD EXACTA + COBERTURA TOTAL. Extrae LAS TAREAS EXACTAS que de verdad haya en la transcripción y en las secciones procesadas, ni una más, ni una menos.

        1. NO AGRUPES TAREAS: Tu principal debilidad es que resumes. Si se mencionan 10 cosas distintas sobre un proyecto, ESO SON 10 TAREAS, no 1 sola agrupada. Compórtate como OpenAI (GPT-4o) que saca la lista exacta de tareas (ej. 23) sin agrupar cosas independientes.
        2. NO INVENTES TAREAS: Solo crea tareas que estén explícita o implícitamente comprometidas en la reunión. No hay un mínimo de tareas estricto, hay que respetar la realidad.
        3. **COBERTURA OBLIGATORIA DE ACUERDOS Y DECISIONES**: Las secciones "ACUERDOS YA PROCESADOS" y "DECISIONES CLAVE YA PROCESADAS" (cuando estén presentes) son la fuente PRIMARIA y más limpia de compromisos. Para CADA frase ahí que implique una acción futura ("se desarrollará X", "se realizará Y", "se acordó hacer Z", "se entregará W", "se validará V"), DEBE existir una tarea correspondiente en tu lista. Esta es la regla más importante: si un acuerdo dice "se desarrollará un manual de marca" → tarea "Desarrollar manual de marca". Si una decisión dice "se entregará el reporte el viernes" → tarea "Entregar reporte" con due_date el viernes.
        4. RESPONSABLES: Si los acuerdos/decisiones no nombran al responsable explícitamente, búscalo en el transcript donde se discute ese tema. Si nadie lo asume, usa "Por asignar" como owner_name.
        5. FECHAS: INFIERE LA FECHA EXACTA basándote en la fecha actual {current_date} y ponla en 'due_date'.
        6. OBLIGATORIO: El campo 'thinking_process' ÚSALO PRIMERO para:
            (a) Listar las tareas detectadas del TRANSCRIPT.
            (b) Listar los compromisos encontrados en ACUERDOS y DECISIONES.
            (c) Verificar que cada compromiso de (b) tiene su tarea correspondiente en (a). Si falta alguna, AGRÉGALA explícitamente.
            (d) Justificar cada tarea final con su cita de origen (transcript o acuerdo).
        7. Todo debe usar el formato estricto (Objetivo, Detalle, etc) de la 'description'.

        Transcripción:
        {safe_transcript}{extra_context}
        """

        schema = self._get_tasks_only_json_schema()
        def strictify_schema(sch):
            if isinstance(sch, dict):
                if sch.get("type") == "object":
                    sch["additionalProperties"] = False
                    if "properties" in sch:
                        for k, v in sch.get("properties", {}).items():
                            strictify_schema(v)
                elif sch.get("type") == "array":
                    if "items" in sch:
                        strictify_schema(sch["items"])
                        
        import copy
        strict_schema = copy.deepcopy(schema)
        strictify_schema(strict_schema)
        
        payload = {
            "model": self.MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Eres un asistente experto. Extrae todas las tareas como se te solicita."
                },
                {"role": "user", "content": prompt}
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "strict": True,
                    "schema": strict_schema
                }
            },
            "temperature": 0.1
        }
        
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = None
            openai_ok = False
            for attempt in range(3):
                try:
                    response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
                except httpx.HTTPError as net_err:
                    logger.warning("OpenAI red/timeout intento %s: %s", attempt + 1, net_err)
                    response = None
                    continue
                if response.status_code == 429 and attempt < 2:
                    wait_seconds = 2 + attempt * 2
                    try:
                        match = re.search(r'try again in (\d+\.?\d*)s', response.text)
                        if match:
                            wait_seconds = float(match.group(1)) + 1.0
                    except (AttributeError, ValueError, TypeError) as parse_err:
                        logger.debug("No se pudo parsear retry-after: %s", parse_err)
                    logger.info("Durmiendo %ss antes de reintentar fallback tareas...", wait_seconds)
                    await asyncio.sleep(wait_seconds)
                    continue
                if response.status_code == 200:
                    openai_ok = True
                    break
                logger.warning("OpenAI API Error: %s", response.text)
                break

            # FALLBACK A GROQ: OpenAI agotó cuota/reintentos (429) o falló.
            # Groq usa una API compatible con OpenAI y tiene cuota aparte —
            # así la generación de tareas NO queda bloqueada por el rate
            # limit de OpenAI. Sin esto el usuario ve "Fallando" y pierde
            # el trabajo. El único uso del botón NO se marca en error, así
            # que puede reintentar; pero preferimos entregar tareas ya.
            if not openai_ok:
                logger.warning(
                    "OpenAI no disponible para tareas (status=%s) — usando "
                    "fallback Groq.",
                    getattr(response, "status_code", "n/a"),
                )
                groq_result = await self._groq_tasks_fallback(client, prompt)
                if groq_result is not None:
                    return groq_result
                # Si Groq también falla, propagamos el error original.
                if response is not None:
                    response.raise_for_status()
                raise RuntimeError(
                    "OpenAI y Groq no disponibles para generar tareas."
                )

            result_json = response.json()
            try:
                content_str = result_json["choices"][0]["message"]["content"]
                parsed_data = json.loads(content_str)
                
                if "properties" in parsed_data and isinstance(parsed_data["properties"], dict) and "action_items" in parsed_data.get("properties", {}):
                    parsed_data = parsed_data["properties"]
                    
                if "action_items" in parsed_data and isinstance(parsed_data["action_items"], dict):
                    if "items" in parsed_data["action_items"]:
                        if isinstance(parsed_data["action_items"]["items"], list):
                            parsed_data["action_items"] = parsed_data["action_items"]["items"]
                        else:
                            parsed_data["action_items"] = []
                    elif "value" in parsed_data["action_items"]:
                        if isinstance(parsed_data["action_items"]["value"], list):
                            parsed_data["action_items"] = parsed_data["action_items"]["value"]
                        else:
                            parsed_data["action_items"] = []
                            
                if isinstance(parsed_data, dict) and len(parsed_data) == 1:
                    first_key = list(parsed_data.keys())[0]
                    if isinstance(parsed_data[first_key], dict) and "action_items" in parsed_data[first_key]:
                        parsed_data = parsed_data[first_key]
                        
                if not isinstance(parsed_data, dict):
                    return {"action_items": []}
                    
                # Clean up thinking_process to prevent cluttering responses
                if "thinking_process" in parsed_data:
                    del parsed_data["thinking_process"]
                    
                return parsed_data
            except Exception as e:
                logger.exception("Error procesando JSON en fallback tareas")
                # Retornamos dict vacío en vez de raise para evitar romper la UI si falla
                return {"action_items": []}

    async def _groq_tasks_fallback(self, client: httpx.AsyncClient, prompt: str) -> Optional[dict]:
        """Fallback de extracción de tareas usando Groq (API compatible
        OpenAI) cuando OpenAI está caído / rate-limited. Devuelve el dict
        con 'action_items' o None si Groq tampoco responde.

        Groq no soporta json_schema strict, así que usamos json_object y
        pedimos el esquema en el prompt. El parser downstream ya es
        tolerante a varias formas del payload."""
        if not settings.groq_api_key:
            logger.warning("GROQ_API_KEY ausente — no hay fallback de tareas.")
            return None
        groq_url = "https://api.groq.com/openai/v1/chat/completions"
        groq_prompt = (
            prompt
            + "\n\nDevuelve EXCLUSIVAMENTE un objeto JSON válido con la forma "
            '{"action_items": [{"title": "...", "description": "...", '
            '"owner_name": "...", "owner_email": "...", "due_date": '
            '"YYYY-MM-DD|null", "due_time": "HH:MM|null", "priority": '
            '"alta|media|baja"}]}. No incluyas texto fuera del JSON.'
        )
        body = {
            "model": _modelos.MODELO_PRINCIPAL,
            "messages": [
                {"role": "system", "content": "Eres un extractor de tareas. Respondes SOLO JSON."},
                {"role": "user", "content": groq_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": 8000,
        }
        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(3):
            try:
                r = await client.post(groq_url, json=body, headers=headers)
            except httpx.HTTPError as net_err:
                logger.warning("Groq fallback red/timeout intento %s: %s", attempt + 1, net_err)
                continue
            if r.status_code == 429 and attempt < 2:
                await asyncio.sleep(2 + attempt * 2)
                continue
            if r.status_code != 200:
                logger.warning("Groq fallback error %s: %s", r.status_code, r.text[:300])
                return None
            try:
                content = r.json()["choices"][0]["message"]["content"]
                data = json.loads(content)
                items = data.get("action_items")
                if isinstance(items, dict):
                    items = items.get("items") or items.get("value") or []
                if not isinstance(items, list):
                    items = []
                logger.info("Groq fallback OK: %s tareas extraídas.", len(items))
                return {"action_items": items}
            except Exception:
                logger.exception("Groq fallback: JSON inválido")
                return None
        return None

    async def _execute_agent(self, client: httpx.AsyncClient, system_prompt: str, user_prompt: str, schema: dict = None, model_override: str = None) -> dict:
        """Helper to execute an LLM agent and safely parse its JSON response"""
        payload = {
            "model": model_override or self.MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "temperature": 0.1
        }
        
        if schema:
            # Para OpenAI Structured Outputs, additionalProperties debe ser False
            def strictify_schema(sch):
                if isinstance(sch, dict):
                    if sch.get("type") == "object":
                        sch["additionalProperties"] = False
                        if "properties" in sch:
                            for k, v in sch.get("properties", {}).items():
                                strictify_schema(v)
                    elif sch.get("type") == "array":
                        if "items" in sch:
                            strictify_schema(sch["items"])
            
            import copy
            strict_schema = copy.deepcopy(schema)
            strictify_schema(strict_schema)
            
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "extraction",
                    "strict": True,
                    "schema": strict_schema
                }
            }
        else:
            payload["response_format"] = {"type": "json_object"}
            
        try:
            # Quitamos el print de error de "Groq" si da timeout.
            response = None
            for attempt in range(3):
                response = await client.post(self.BASE_URL, json=payload, headers=self.headers, timeout=120.0)
                if response.status_code == 429:
                    logger.warning("Intento %s - OpenAI 429 ratelimit: %s", attempt+1, response.text)
                    if attempt < 2:
                        wait_seconds = 2 + attempt * 2
                        try:
                            match = re.search(r'try again in (\d+\.?\d*)s', response.text)
                            if match:
                                wait_seconds = float(match.group(1)) + 1.0
                        except (AttributeError, ValueError, TypeError) as parse_err:
                            logger.debug("No se pudo parsear retry-after: %s", parse_err)
                        logger.info("Durmiendo %ss antes de reintentar...", wait_seconds)
                        await asyncio.sleep(wait_seconds)
                        continue
                response.raise_for_status()
                break
            result_json = response.json()
            usage = result_json.get("usage") or {}
            if usage:
                logger.info(
                    "OPENAI_TOKENS_USED model=%s prompt=%s completion=%s total=%s",
                    payload.get("model"),
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    usage.get("total_tokens"),
                )
            content_str = result_json["choices"][0]["message"]["content"]
            parsed_data = json.loads(content_str)

            # Root wrap defensive programming ("properties", "response", etc)
            if "properties" in parsed_data and isinstance(parsed_data["properties"], dict):
                for key in ["summary", "action_items", "language", "decisions", "themes"]:
                    if key in parsed_data.get("properties", {}):
                        parsed_data = parsed_data["properties"]
                        break
                        
            if isinstance(parsed_data, dict) and len(parsed_data) == 1:
                first_key = list(parsed_data.keys())[0]
                if isinstance(parsed_data[first_key], dict):
                    for key in ["summary", "action_items", "language", "decisions", "themes"]:
                        if key in parsed_data[first_key]:
                            parsed_data = parsed_data[first_key]
                            break
                            
            # Value or Description wrapper defensive programming 
            for key in ["summary", "decisions", "risks", "agreements"]:
                if key in parsed_data and isinstance(parsed_data[key], dict):
                    if "value" in parsed_data[key]:
                        parsed_data[key] = parsed_data[key]["value"]
                    elif "description" in parsed_data[key]:
                        # Handle schema hallucination where LLM returns {"type": "...", "description": "Respuesta"}
                        parsed_data[key] = parsed_data[key]["description"]
                    
            for key in ["attendees", "themes", "action_items"]:
                if key in parsed_data and isinstance(parsed_data[key], dict):
                    if "items" in parsed_data[key]:
                        if isinstance(parsed_data[key]["items"], list):
                            parsed_data[key] = parsed_data[key]["items"]
                        elif isinstance(parsed_data[key]["items"], dict) and "properties" in parsed_data[key]["items"]:
                            parsed_data[key] = []
                    elif "value" in parsed_data[key]:
                        if isinstance(parsed_data[key]["value"], list):
                            parsed_data[key] = parsed_data[key]["value"]
                        else:
                            parsed_data[key] = []
                            
            if not isinstance(parsed_data, dict):
                return {}
                
            return parsed_data
        except Exception as e:
            logger.exception("Error procesando JSON de un agente LLM")
            return {}

    async def process_transcript(
        self,
        transcript: str,
        project_contacts: list = None,
        output_language: str = "es",
    ) -> dict:
        """
        Arquitectura Multi-Agente: Ejecuta 3 promps paralelos para evitar el Colapso de Contexto (Context Collapse)
        y garantizar extrema fidelidad y volumen en cada sección (Fundamentales, Insights, Tareas).

        output_language ('es'|'ca'|'en') fuerza el idioma de salida de TODO
        el contenido generado (summary, attendees roles, themes, decisions,
        risks, agreements, tasks), independiente del idioma del transcript.
        """
        _lang_name = self._resolve_lang_name(output_language)
        safe_transcript = transcript # REMOVED TRUNCATION, GPT-4o handles 128k context natively to catch all tasks
            
        contacts_info = ""
        if project_contacts:
            contacts_str = json.dumps(project_contacts, ensure_ascii=False)
            contacts_info = f"\nTienes acceso a la siguiente lista de personas del proyecto:\n{contacts_str}\n"

        current_date = datetime.now().strftime("%Y-%m-%d")

        # --- Base Prompts ---
        system_base = f"""Eres un coordinador de proyecto experto analizando una reunión. REGLA DE ORO: TUS RESPUESTAS DEBEN SER EXCLUSIVAMENTE EN {_lang_name}, INDEPENDIENTEMENTE DEL IDIOMA DE LA REUNIÓN. La fecha actual es {current_date} (año {current_date.split('-')[0]})."""
        
        # AGENT 1: Fundamentals (Language, Summary, Attendees, Themes)
        prompt_fundamentals = f"""
        Analiza el texto y extrae:
        - Idioma original (language). Todo lo demás de tu JSON debe estar en {_lang_name}.
        - Un resumen ('summary') muy extenso, denso y profundo de toda la reunión (mínimo 4 o 5 párrafos ricos en contexto).
        - Participantes ('attendees'). REGLA OBLIGATORIA: Extrae A TODOS LOS PARTICIPANTES mencionados en la reunión o transcripción, sin importar cuántos sean. Usa la 'lista de personas del proyecto' REGLA DE ORO SOLAMENTE COMO APOYO para enriquecer los datos (copiando su 'role' y 'entity' de la DB si los identificas ahí), pero SI NO ESTÁN EN LA LISTA, extraelos igual e infiere su rol y entidad por contexto. NUNCA limites la extracción a la lista.
        - Los temas discutidos ('themes') y sus elaborados puntos de conversación.
        
        {contacts_info}
        
        Transcripción:
        {safe_transcript}
        """
        schema_fund = self._get_fundamentals_schema()
        
        # AGENT 2: Insights (Decisions, Risks, Agreements) — formato viñetado
        # con responsable explícito por línea. El lector tiene 30 seg.
        prompt_insights = f"""
        Como redactor experto en actas corporativas, devuelve los siguientes
        campos COMO LISTAS MARKDOWN (`-` por línea), NUNCA párrafos largos:

        - 'decisions':
            Una viñeta por decisión, formato exacto:
              `- **[Tema]** Decisión concreta — Responsable: <Nombre o 'por definir'>`
            Si necesitas contexto, segunda línea sangrada con `  · contexto: <una frase>`.

        - 'risks':
            Una viñeta por riesgo:
              `- **[Severidad: alta/media/baja]** Riesgo concreto — Responsable de seguimiento: <Nombre>`
            Si la reunión no menciona riesgos, devuelve `- Sin riesgos identificados.`

        - 'agreements':
            Una viñeta por acuerdo:
              `- **[Tema]** Acuerdo concreto — Partes: <Nombres>`

        Reglas duras:
        1. NUNCA generes párrafos largos. SIEMPRE viñetas.
        2. Cada viñeta debe nombrar al responsable. Si no se identifica, usa
           "por definir".
        3. Sé conciso: el lector debe entender el acta en 30 segundos.
        4. No inventes responsables ni cifras que no estén en la transcripción.

        Transcripción:
        {safe_transcript}
        """
        schema_ins = self._get_insights_schema()

        # AGENT 3: Tasks — el prompt se construye DESPUÉS de ejecutar el
        # agent de insights, para incluir como contexto los acuerdos y
        # decisiones ya destilados (no solo el transcript ruidoso). Ver
        # el bloque async with abajo.
        schema_tasks = self._get_tasks_only_json_schema()

        async with httpx.AsyncClient(timeout=180.0) as client:
            # Secuencial (no parallel) para evitar 429 Too Many Requests.
            results = []

            # Agent 1: Fundamentals
            res_fund = await self._execute_agent(
                client, system_base, prompt_fundamentals, schema_fund,
                model_override="gpt-4o-mini",
            )
            results.append(res_fund)

            # Agent 2: Insights (decisions, risks, agreements)
            res_ins = await self._execute_agent(
                client, system_base, prompt_insights, schema_ins,
                model_override="gpt-4o-mini",
            )
            results.append(res_ins)

            # Bloque de contexto extra para el agent de tareas: pasamos
            # las decisiones y acuerdos que el agent 2 acaba de destilar.
            # Esto resuelve el bug donde compromisos claros en Acuerdos
            # se perdían porque el LLM solo veía el transcript ruidoso.
            insights_decisions  = (res_ins.get("decisions", "")  if isinstance(res_ins, dict) else "")
            insights_agreements = (res_ins.get("agreements", "") if isinstance(res_ins, dict) else "")
            extra_blocks = []
            if (insights_decisions or "").strip():
                extra_blocks.append(
                    "=== DECISIONES YA EXTRAÍDAS POR EL ANALISTA ===\n"
                    + insights_decisions.strip()
                )
            if (insights_agreements or "").strip():
                extra_blocks.append(
                    "=== ACUERDOS YA EXTRAÍDOS (FUENTE PRIMARIA DE COMPROMISOS) ===\n"
                    + insights_agreements.strip()
                )
            extra_context = (
                "\n\n" + "\n\n".join(extra_blocks)
                if extra_blocks else ""
            )

            # Agent 3: Tasks — ahora con visibilidad de acuerdos/decisiones
            # ya curados. Verifica cobertura cruzando contra ellos.
            prompt_tasks = f"""
        INSTRUCCIONES CLAVE PARA TAREAS - FIDELIDAD ABSOLUTA + COBERTURA TOTAL:
        DATO: La fecha actual es {current_date}.
        {contacts_info}
        1. REGLA OBLIGATORIA: Extrae las tareas EXACTAS que tiene la transcripción. Ni inventes cuotas arbitrarias ni omitas cosas reales.
        2. NO AGRUPES: Desglosa todo en sus tareas atómicas sin agrupar detalles independientes, logrando la misma exactitud microscópica que lograría OpenAI GPT-4.
        3. **COBERTURA OBLIGATORIA DE ACUERDOS Y DECISIONES**: Las secciones "ACUERDOS YA EXTRAÍDOS" y "DECISIONES YA EXTRAÍDAS" (cuando estén presentes) son la fuente PRIMARIA y más limpia de compromisos. Para CADA viñeta ahí que implique una acción futura ("se desarrollará X", "se realizará Y", "se acordó hacer Z", "se entregará W", "se validará V"), DEBE existir una tarea correspondiente en tu lista. Es la regla más importante: si un acuerdo dice "se desarrollará un manual de marca" → tarea "Desarrollar manual de marca". Si una decisión dice "se entregará el reporte el viernes" → tarea "Entregar reporte" con due_date el viernes.
        4. RESPONSABLES: Si los acuerdos/decisiones no nombran responsable, búscalo en el transcript donde se discute ese tema. Si nadie lo asume, usa "Por asignar" como owner_name.
        5. 'thinking_process': úsalo PRIMERO para:
            (a) Listar tareas detectadas en el TRANSCRIPT.
            (b) Listar compromisos en ACUERDOS y DECISIONES.
            (c) Verificar que cada compromiso de (b) tiene su tarea en (a). Si falta alguna, AGRÉGALA explícitamente.
            (d) Justificar cada tarea final con su cita de origen.
        6. FECHAS: INFIERE la fecha exacta de 'due_date' calculando desde {current_date}.
        7. ESPECIFICIDAD Y ESTRUCTURA: Usa estrictamente la plantilla de 'description' para CADA TAREA.

        Transcripción:
        {safe_transcript}{extra_context}
        """
            res_tasks = await self._execute_agent(
                client, system_base, prompt_tasks, schema_tasks,
            )  # Este sí usará gpt-4o
            results.append(res_tasks)

            # Merge the dicts
            merged_payload = {}
            for res in results:
                if isinstance(res, dict):
                    merged_payload.update(res)

            # Limpiar rastro de IA cognitiva
            if "thinking_process" in merged_payload:
                del merged_payload["thinking_process"]

            return merged_payload

    async def deduce_project(self, summary: str, projects: List[Dict[str, Any]]) -> int | None:
        """
        Deduce a qué proyecto pertenece una reunión basándose en el summary y la lista de proyectos.
        Retorna el ID del proyecto o None.
        """
        if not projects or not summary:
            return None
        
        projects_str = json.dumps(projects, ensure_ascii=False)
        prompt = f"""
        Eres un asistente de clasificación súper preciso. Tienes el siguiente resumen de una reunión transcrita:
        '{summary}'
        
        Y la siguiente lista de proyectos disponibles en formato JSON (junto con sus descripciones):
        {projects_str}
        
        Deduce de qué proyecto de la lista se estaba hablando en la reunión basandote en la intuición de los textos.
        Compara los temas tratados con el nombre y la descripción de cada proyecto.
        Responde exclusivamente con un JSON que contenga la propiedad 'project_id' (entero) con el id del proyecto que encaja. Si definitivamente no hace sentido con ninguno, responde null.
        """
        
        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": "Responde EXCLUSIVAMENTE en formato JSON estricto con la siguiente estructura: {\"project_id\": integer | null}"},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.0
        }
        
        async with httpx.AsyncClient(timeout=None) as client:
            try:
                response = None
                for attempt in range(3):
                    response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
                    if response.status_code == 429 and attempt < 2:
                        wait_seconds = 2 + attempt * 2
                        try:
                            match = re.search(r'try again in (\d+\.?\d*)s', response.text)
                            if match:
                                wait_seconds = float(match.group(1)) + 1.0
                        except (AttributeError, ValueError, TypeError) as parse_err:
                            logger.debug("No se pudo parsear retry-after: %s", parse_err)
                        logger.info("Durmiendo %ss antes de reintentar deducir proyecto...", wait_seconds)
                        await asyncio.sleep(wait_seconds)
                        continue
                    response.raise_for_status()
                    break
                content_str = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content_str)
                return parsed.get("project_id")
            except Exception as e:
                logger.exception("Error deduciendo proyecto")
                return None

    async def translate_and_clean_summary(self, dirty_summary: str) -> str:
        """
        Limpia un resumen generado por una IA externa (ej. Fireflies Daily Digest),
        traduce los títulos al español, elimina encabezados redundantes y asteriscos markdown.
        """
        prompt = f"""
        Eres un especialista en edición de actas corporativas de alto nivel gerencial.
        A continuación se te proporciona un resumen de reunión generado por otra herramienta de IA (como Fireflies), el cual contiene títulos redundantes, encabezados en inglés (como TOPICS, BLOCKERS) y metadatos sucios (como referencias de **[Fuente: ...]**).
        
        Tus Reglas Estrictas:
        1. TRADUCE todos los encabezados al español profesional (ej. "Temas Principales", "Bloqueos y Retrasos").
        2. ELIMINA por completo títulos introductorios redundantes que digan cosas como "Daily Digest Resumen Ejecutivo Consolidado — Nombre de Proyecto...". Entra directamente a la estructura de la información, el acta ya tiene el título.
        3. ELIMINA referencias literales de fuentes o corchetes tipo `**[Fuente: nombre, fecha]**`. Queremos el puro contenido.
        4. ELIMINA asteriscos literales (`**`) dejándolo en texto plano amigable sin ensuciar la visualización corporativa.
        5. Respeta al 100% los hechos, los responsables, las fechas y los puntos clave discutidos. Solo pule la forma.
        
        Texto Original Sucio:
        {dirty_summary}
        
        Responde INMEDIATAMENTE y EXCLUSIVAMENTE con el nuevo texto documentado. No agregues introducciones tuyas ni explicaciones de lo que editaste.
        """
        
        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2
        }
        
        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = None
                for attempt in range(3):
                    response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
                    if response.status_code == 429 and attempt < 2:
                        wait_seconds = 2 + attempt * 2
                        try:
                            match = re.search(r'try again in (\d+\.?\d*)s', response.text)
                            if match:
                                wait_seconds = float(match.group(1)) + 1.0
                        except (AttributeError, ValueError, TypeError) as parse_err:
                            logger.debug("No se pudo parsear retry-after: %s", parse_err)
                        logger.info("Durmiendo %ss antes de reintentar limpiar resumen...", wait_seconds)
                        await asyncio.sleep(wait_seconds)
                        continue
                    response.raise_for_status()
                    break
                return response.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                logger.exception("Error limpiando resumen")
                return dirty_summary  # Fallback al original si falla


# Alias retro-compatible: el código heredado importa `GroqService` desde este módulo.
GroqService = OpenAIService

