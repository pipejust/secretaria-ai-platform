import json
from base64 import b64encode
import httpx
from typing import Dict, Any, List

from config import settings
from models import ActionItem

class GroqService:
    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.3-70b-versatile" # Groq soporta multiples, este es bueno para schemas
    
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {settings.groq_api_key}",
            "Content-Type": "application/json"
        }
        
    def _get_json_schema(self) -> Dict[str, Any]:
        """Define la estructura estricta que esperamos de la transcripción"""
        return {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "decisions": {"type": "string"},
                "risks": {"type": "string"},
                "agreements": {"type": "string"},
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
                },
                "action_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "owner_name": {"type": "string"},
                            "owner_email": {"type": "string"},
                            "title": {"type": "string"},
                            "description": {"type": "string"},
                            "due_date": {"type": "string", "description": "Formato YYYY-MM-DD"}
                        },
                        "required": ["owner_name", "owner_email", "title", "description"]
                    }
                }
            },
            "required": ["summary", "decisions", "risks", "agreements", "attendees", "themes", "action_items"]
        }

    async def process_transcript(self, transcript: str, project_contacts: list = None) -> dict:
        """
        Envía el transcript completo a Groq para extraer información estructurada
        basada en el JSON schema.
        """
        # Groq Llama 3 70b has an 8k token limit. A full hour transcript can exceed this, causing a 400 error.
        # We safely truncate to the last 25,000 characters (approx 5000 tokens) porque action items y summaries
        # suelen estar al final.
        safe_transcript = transcript
        if len(transcript) > 25000:
            safe_transcript = transcript[:3000] + "\n\n[... TEXTO RECORTADO POR LONGITUD ...]\n\n" + transcript[-21000:]
            
        contacts_info = ""
        if project_contacts:
            contacts_str = json.dumps(project_contacts, ensure_ascii=False)
            contacts_info = f"\n\nTienes acceso a la siguiente lista de personas del proyecto:\n{contacts_str}\nSi una tarea es asignada a una persona de esta lista, debes usar su 'name' y 'email' exactos.\n"

        prompt = f"""
        Eres un asistente experto que procesa transcripciones de reuniones.
        Analiza el siguiente texto y extrae un resumen general EXTENSO y DETALLADO, los TEMAS ESPECÍFICOS tratados con sus detalles correspondientes, las decisiones clave globales, los riesgos identificados, los acuerdos generales, las TAREAS accionables y los ASISTENTES de la reunión.
        
        INSTRUCCIONES CLAVE PARA EL RESUMEN ('summary'):
        NUNCA seas breve. Debes crear un resumen extenso, minucioso y muy detallado de toda la reunión, abarcando contexto, problemas identificados, soluciones propuestas y próximos pasos (mínimo unos 3 o 4 párrafos poblados).
        
        INSTRUCCIONES CLAVE PARA DECISIONES, RIESGOS Y ACUERDOS:
        - 'decisions': Extrae y detalla extensamente todas las decisiones clave globales que se tomaron durante la reunión. Si no hay, pon "No se registraron decisiones clave."
        - 'risks': Identifica y explica cualquier riesgo, problema potencial, cuello de botella o dependencia bloqueante mencionada. Si no hay, pon "No se identificaron riesgos."
        - 'agreements': Enumera los acuerdos generales a los que llegó el equipo (diferente a tareas, son consensos). Si no hay, pon "No se registraron acuerdos generales."
        
        INSTRUCCIONES CLAVE PARA ASISTENTES ('attendees'):
        Extrae los nombres de las personas que participaron. Si se menciona su cargo o la empresa a la que pertenecen, inclúyelo también.
        
        INSTRUCCIONES CLAVE PARA TEMAS ('themes'):
        Divide la reunión en los diferentes temas puntuales que se trataron. Para cada tema, extrae una lista viñeteada de los puntos de discusión específicos ('discussion_points').
        
        PRECAUCIÓN MUY IMPORTANTE SOBRE BÚSQUEDA DE CORREOS:
        Intenta identificar y extraer los correos electrónicos mencionados para asignarlos a 'owner_email'. {contacts_info}
        
        INSTRUCCIONES CLAVE PARA TAREAS (ACTION ITEMS) - ¡MUY IMPORTANTE!:
        1. Analiza cuidadosamente la transcripción palabra por palabra buscando TODO compromiso, tarea, solicitud o acción futura que alguna persona deba realizar.
        2. NO esperes encontrar un bloque explicito que diga "Tareas" o "Action Items". Debes extraer las tareas IMPLÍCITAMENTE de la conversación natural (ej. "Yo te envío ese correo mañana", "Revisemos esto el viernes", "Cristian valida eso").
        3. DEBES SEPARAR tareas compuestas en tareas individuales por cada accion concreta.
        4. Para CADA tarea identificada, DEBES generar obligatoriamente un OBJETO JSON con: 'owner_name', 'owner_email', 'title', 'description', y 'due_date' (si se menciona o deduce).
        5. IMPORTANTE: En el 99% de las reuniones de trabajo hay tareas. Solo si ESTÁS ABSOLUTAMENTE SEGURO de que no hubo NINGÚN compromiso futuro, retorna un arreglo Vacío []. NUNCA retornes un arreglo de strings.
        
        Transcripción:
        {safe_transcript}
        """

        payload = {
            "model": self.MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1
        }
        
        # Hay diferentes formas de forzar schema. Aquí usamos un prompt fuerte combinado con json_object.
        # Alternativamente podríamos usar el endpoint nativo de structured outputs the Groq si la libreria oficial estuviera disponible y configurada.
        # Pero con requests puras, anexamos el esquema esperado.
        payload["messages"].insert(0, {
            "role": "system",
            "content": f"Responde EXCLUSIVAMENTE en formato JSON con la siguiente estructura: {json.dumps(self._get_json_schema())}"
        })

        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(self.BASE_URL, json=payload, headers=self.headers)
            if response.status_code != 200:
                print(f"Groq API Error: {response.text}")
            response.raise_for_status()
            
            result_json = response.json()
            try:
                content_str = result_json["choices"][0]["message"]["content"]
                parsed_data = json.loads(content_str)
                
                # Defensive check: Llama sometimes wraps output in "properties" because of the schema prompt
                if "properties" in parsed_data and isinstance(parsed_data["properties"], dict) and "summary" in parsed_data.get("properties", {}):
                    parsed_data = parsed_data["properties"]
                
                # Defensive check 2: if it wrapped it in a single root object like {"response": {...}}
                if isinstance(parsed_data, dict) and len(parsed_data) == 1:
                    first_key = list(parsed_data.keys())[0]
                    if isinstance(parsed_data[first_key], dict) and ("summary" in parsed_data[first_key] or "action_items" in parsed_data[first_key]):
                        parsed_data = parsed_data[first_key]
                        
                if not isinstance(parsed_data, dict):
                    return {"summary": str(parsed_data)}
                    
                return parsed_data
            except Exception as e:
                # Si falla el parseo, lanzar error
                print(f"Error procesando JSON de Groq: {result_json}")
                raise e

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
