import json
import asyncio
from base64 import b64encode
import httpx
from typing import Dict, Any, List

from config import settings
from models import ActionItem
from datetime import datetime

class GroqService:
    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.3-70b-versatile" # Groq soporta multiples, este es bueno para schemas
    
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json"
        }
        
    async def transcribe_audio(self, file_bytes: bytes, filename: str) -> str:
        """Transcribe an audio file using Groq's Whisper."""
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        
        # Determine language or default to multilingüe for Whisper
        # We use a Multipart form data request
        files = {
            "file": (filename, file_bytes, "audio/mpeg")
        }
        data = {
            "model": "whisper-large-v3",
            "response_format": "json"
        }
        
        headers = {
            "Authorization": f"Bearer {settings.groq_api_key}"
            # Do NOT set Content-Type to application/json, httpx will set multipart/form-data automatically
        }
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                response = await client.post(url, files=files, data=data, headers=headers)
                response.raise_for_status()
                return response.json().get("text", "")
            except Exception as e:
                import traceback
                print(f"Error transcribiendo audio con Groq: {e}\n{traceback.format_exc()}")
                raise Exception(f"Fallo en la transcripción de audio: {e}")
        
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
                        "required": ["name"]
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
        """Schema para Agent 2: Textos narrativos profundos"""
        return {
            "type": "object",
            "properties": {
                "decisions": {"type": "string", "description": "TEXTO EXHAUSTIVO Y GIGANTE (mínimo 3 a 5 párrafos grandes, NO listas ni viñetas). Analiza todas las decisiones, sus motivaciones y el contexto con lujo de detalles."},
                "risks": {"type": "string", "description": "TEXTO EXHAUSTIVO Y GIGANTE (mínimo 3 a 5 párrafos grandes, NO listas ni viñetas). Explica profundamente cada riesgo, bloqueo o preocupación detectada, su gravedad y contexto."},
                "agreements": {"type": "string", "description": "TEXTO EXHAUSTIVO Y GIGANTE (mínimo 3 a 5 párrafos grandes, NO listas ni viñetas). Detalla largamente y en prosa todos los acuerdos generales y consensos logrados."}
            },
            "required": ["decisions", "risks", "agreements"]
        }

    def _get_tasks_only_json_schema(self) -> Dict[str, Any]:
        """Define la estructura estricta enfocada exclusivamente en tareas para no diluir el contexto de la IA"""
        return {
            "type": "object",
            "properties": {
                "thinking_process": {
                    "type": "string",
                    "description": "PASO 1: HAZ UN ANÁLISIS RENGLÓN POR RENGLÓN de toda la transcripción para identificar ABSOLUTAMENTE TODAS las tareas reales y compromisos mencionados. NO INVENTES NI REPITAS TAREAS. Anota aquí cada compromiso real encontrado antes de pasar a action_items."
                },
                "action_items": {
                    "type": "array",
                    "description": "PASO 2: Lista detallada en formato JSON de TODAS las tareas individuales identificadas. ¡EXTRAE SÓLO TAREAS REALES MENCIONADAS EN EL TEXTO, NO ALUCINES NI INVENTES TAREAS, NO LAS AGRUPES!",
                    "items": {
                        "type": "object",
                        "properties": {
                            "owner_name": {"type": "string"},
                            "owner_email": {"type": "string"},
                            "title": {"type": "string", "description": "Título claro y descriptivo de la tarea específica."},
                            "description": {"type": "string", "description": "Usa ESTRICTAMENTE el siguiente formato separador con saltos de línea y texto para estructurar esta tarea específica:\nObjetivo: [texto]\nDetalle específico: [texto]\nActividades puntuales: [texto]\nEntregable: [texto]\nCriterio de cierre: [texto]"},
                            "due_date": {"type": "string", "description": "Revisa tu thinking_process para colocar la fecha o día exacto acordado en formato YYYY-MM-DD."}
                        },
                        "required": ["owner_name", "owner_email", "title", "description", "due_date"]
                    }
                }
            },
            "required": ["thinking_process", "action_items"]
        }

    async def process_transcript_for_tasks_only(self, transcript: str, project_contacts: list = None) -> dict:
        """
        Envía el transcript a Groq pidiendo EXCLUSIVAMENTE action_items.
        Ésto permite que la IA dedique todos sus tokens/atención a generar tareas altamente detalladas
        y no pierda calidad al regenerar.
        """
        safe_transcript = transcript
        if len(transcript) > 25000:
            safe_transcript = transcript[:3000] + "\n\n[... TEXTO RECORTADO POR LONGITUD ...]\n\n" + transcript[-21000:]
            
        contacts_info = ""
        if project_contacts:
            contacts_str = json.dumps(project_contacts, ensure_ascii=False)
            contacts_info = f"\n\nTienes acceso a la siguiente lista de personas del proyecto:\n{contacts_str}\nSi una tarea es asignada a una persona de esta lista, debes usar su 'name' y 'email' exactos.\n"

        current_date = datetime.now().strftime("%Y-%m-%d")

        prompt = f"""
        Eres un asistente experto que procesa transcripciones de reuniones internacionales.
        Tu ÚNICO OBJETIVO es extraer los compromisos y tareas con el MÁXIMO detalle posible.
        
        ¡MUY IMPORTANTE - REGLA DE ORO!: SIN IMPORTAR EL IDIOMA DE LA TRANSCRIPCIÓN, LAS TAREAS DEBEN SER GENERADAS EXCLUSIVAMENTE Y ESTRICTAMENTE EN ESPAÑOL.
        
        DATO CLAVE DE CONTEXTO TEMPORAL:
        La fecha actual es {current_date}. Utiliza esta información para inferir correctamente los años y fechas relativas (ej. si dicen "el próximo martes" o "para el 15 de marzo", usa el año actual o el correspondiente). NUNCA asumas años pasados si no se dicen explícitamente.
        
        PRECAUCIÓN MUY IMPORTANTE SOBRE BÚSQUEDA DE CORREOS:
        Intenta identificar y extraer los correos electrónicos mencionados para asignarlos a 'owner_email'. {contacts_info}
        
        INSTRUCCIONES CLAVE PARA TAREAS (ACTION ITEMS) - ¡MUY IMPORTANTE!:
        Eres un analista implacable que lee la transcripción renglón por renglón. Tu meta es extraer ABSOLUTAMENTE TODAS las tareas reales o compromisos explícitamente mencionados, por ínfimos que parezcan. EXPLICA CADA TAREA CON MÁXIMO GRADO DE DETALLE.
        1. REGLA DE ORO ANTI-ALUCINACIÓN: NO inventes tareas que no estén en el texto. NO repitas tareas. Si la reunión es corta, extrae solo lo real. NO AGRUPES LAS TAREAS, mantenlas individuales.
        2. FECHAS: Muchísimas tareas tienen fecha o límites de tiempo mencionados. Busca pistas como "la próxima semana". INFIERE LA FECHA EXACTA basándote en la fecha actual {current_date} (año {current_date.split('-')[0]}) y ponla en 'due_date'.
        3. Obligatorio llenar el campo 'thinking_process' PRIMERO como un borrador larguísimo documentando la lógica de por qué consideras que cada punto es una tarea real.
        4. Las descripciones de TODAS LAS TAREAS INDIVIDUALES DEBEN usar estrictamente la plantilla de formato requerida en la propiedad 'description' del esquema JSON. NO PUEDES OMITIR NINGUNA PARTE DE LA PLANTILLA.
        
        Transcripción:
        {safe_transcript}
        """

        payload = {
            "model": self.MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": f"Responde EXCLUSIVAMENTE en formato JSON con la siguiente estructura: {json.dumps(self._get_tasks_only_json_schema())}"
                },
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }
        
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
            if response.status_code != 200:
                print(f"Groq API Error: {response.text}")
            response.raise_for_status()
            
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
                print(f"Error procesando JSON de Groq en fallback tareas: {result_json}\nTransito Fallido: {str(e)}")
                # Retornamos dict vacío en vez de raise para evitar romper la UI si falla
                return {"action_items": []}

    async def _execute_agent(self, client: httpx.AsyncClient, system_prompt: str, user_prompt: str) -> dict:
        """Helper to execute an LLM agent and safely parse its JSON response"""
        payload = {
            "model": self.MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }
        
        try:
            response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
            response.raise_for_status()
            result_json = response.json()
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
            print(f"Error procesando JSON de un Agente Groq: {str(e)}")
            return {}

    async def process_transcript(self, transcript: str, project_contacts: list = None) -> dict:
        """
        Arquitectura Multi-Agente: Ejecuta 3 promps paralelos para evitar el Colapso de Contexto (Context Collapse)
        y garantizar extrema fidelidad y volumen en cada sección (Fundamentales, Insights, Tareas).
        """
        safe_transcript = transcript
        if len(transcript) > 25000:
            safe_transcript = transcript[:3000] + "\n\n[... TEXTO RECORTADO POR LONGITUD ...]\n\n" + transcript[-21000:]
            
        contacts_info = ""
        if project_contacts:
            contacts_str = json.dumps(project_contacts, ensure_ascii=False)
            contacts_info = f"\nTienes acceso a la siguiente lista de personas del proyecto:\n{contacts_str}\n"

        current_date = datetime.now().strftime("%Y-%m-%d")

        # --- Base Prompts ---
        system_base = f"""Eres un coordinador de proyecto experto analizando una reunión. REGLA DE ORO: TUS RESPUESTAS DEBEN SER EXCLUSIVAMENTE EN ESPAÑOL, INDEPENDIENTEMENTE DEL IDIOMA DE LA REUNIÓN. La fecha actual es {current_date} (año {current_date.split('-')[0]})."""
        
        # AGENT 1: Fundamentals (Language, Summary, Attendees, Themes)
        prompt_fundamentals = f"""
        Analiza el texto y extrae:
        - Idioma original (language). Todo lo demás de tu JSON debe estar en ESPAÑOL.
        - Un resumen ('summary') muy extenso, denso y profundo de toda la reunión (mínimo 4 o 5 párrafos ricos en contexto).
        - Participantes ('attendees'), sus cargos y empresas si se mencionan.
        - Los temas discutidos ('themes') y sus elaborados puntos de conversación.
        
        Transcripción:
        {safe_transcript}
        """
        sys_fundamentals = f"{system_base} Responde EXCLUSIVAMENTE en formato JSON con la siguiente estructura: {json.dumps(self._get_fundamentals_schema())}"
        
        # AGENT 2: Insights (Decisions, Risks, Agreements)
        prompt_insights = f"""
        Como redactor experto en actas:
        - 'decisions': REDACTA UN TEXTO MONUMENTAL, GIGANTE Y FLUIDO (mínimo 3 a 5 párrafos grandes, SIN VIÑETAS) contando con muchísimo detalle TODAS las decisiones clave tomadas, motivos y resultados. 
        - 'risks': REDACTA UN TEXTO EN PROSA GIGANTE (mínimo 3 a 5 párrafos grandes, SIN VIÑETAS) explicando de forma altamente granular los bloqueos o preocupaciones mencionadas.
        - 'agreements': REDACTA UN TEXTO PROFUNDO Y EXTENSO (mínimo 3 a 5 párrafos grandes, SIN VIÑETAS) con las metodologías, consensos generales o fechas límite holísticas, con máximo contexto.
        ES OBLIGATORIO que los tres textos sean MUY LARGOS, descriptivos, y llenos de contexto. El usuario odia las viñetas y odia los resúmenes cortos, desarrolla ideas largas y completas.
        
        Transcripción:
        {safe_transcript}
        """
        sys_insights = f"{system_base} Responde EXCLUSIVAMENTE en formato JSON con la siguiente estructura: {json.dumps(self._get_insights_schema())}"

        # AGENT 3: Tasks (Action Items + Thinking Process JSON Chain of Thought)
        prompt_tasks = f"""
        INSTRUCCIONES CLAVE PARA TAREAS:
        DATO: La fecha actual es {current_date}. 
        {contacts_info}
        1. ANÁLISIS RENGLÓN POR RENGLÓN: Lee y procesa la transcripción línea por línea.
        2. EXTRACCIÓN FIEL Y DETALLADA: Extrae TODAS las tareas y compromisos explícitamente mencionados, por ínfimos que parezcan. REGLA ANTI-ALUCINACIÓN: NO INVENTES NI REPITAS TAREAS. EXPANDE EL DETALLE DE CADA UNA AL MÁXIMO. ¡NO LAS AGRUPES!
        3. 'thinking_process': ÚSalo PRIMERO en tu JSON. Es un diario detallado donde analizas línea a línea por qué cada iniciativa es una tarea real ANTES de pasarla al arreglo.
        4. FECHAS: INFIERE la fecha exacta de 'due_date' calculando desde la fecha actual {current_date}.
        5. ESPECIFICIDAD Y ESTRUCTURA: Usa estrictamente la plantilla de formato requerida en la propiedad 'description' del esquema JSON (Objetivo, Detalle, Actividades puntuales, Entregable, Criterio de cierre) rellenando cada campo con abundante información contextual para CADA UNA de las tareas individuales extraídas.
        
        Transcripción:
        {safe_transcript}
        """
        sys_tasks = f"{system_base} Responde EXCLUSIVAMENTE en formato JSON con la siguiente estructura: {json.dumps(self._get_tasks_only_json_schema())}"

        async with httpx.AsyncClient(timeout=None) as client:
            aws = [
                self._execute_agent(client, sys_fundamentals, prompt_fundamentals),
                self._execute_agent(client, sys_insights, prompt_insights),
                self._execute_agent(client, sys_tasks, prompt_tasks)
            ]
            results = await asyncio.gather(*aws)
            
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
                response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
                response.raise_for_status()
                content_str = response.json()["choices"][0]["message"]["content"]
                parsed = json.loads(content_str)
                return parsed.get("project_id")
            except Exception as e:
                print(f"Error deduciendo proyecto en Groq: {e}")
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
                response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"].strip()
            except Exception as e:
                print(f"Error limpiando resumen en Groq: {e}")
                return dirty_summary # Fallback al puro original si llegara a fallar

