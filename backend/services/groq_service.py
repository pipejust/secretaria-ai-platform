import json
from base64 import b64encode
import httpx
from typing import Dict, Any, List

from config import settings
from models import ActionItem

class GroqService:
    BASE_URL = "https://api.groq.com/openai/v1/chat/completions"
    MODEL = "llama-3.1-70b-versatile" # Groq soporta multiples, este es bueno para schemas
    
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
            "required": ["summary", "decisions", "risks", "agreements", "action_items"]
        }

    async def process_transcript(self, transcript: str, project_contacts: list = None) -> dict:
        """
        Envía el transcript completo a Groq para extraer información estructurada
        basada en el JSON schema.
        """
        # Groq Llama 3 70b has an 8k token limit. A full hour transcript can exceed this, causing a 400 error.
        # We safely truncate to the last 25,000 characters (approx 5000 tokens) because action items and summaries
        # are heavily weighted towards the end of the meeting, but we also include the first 5000 chars for context.
        safe_transcript = transcript
        if len(transcript) > 25000:
            safe_transcript = transcript[:5000] + "\n\n[... TRUNCATED ...]\n\n" + transcript[-20000:]
            
        contacts_info = ""
        if project_contacts:
            contacts_str = json.dumps(project_contacts, ensure_ascii=False)
            contacts_info = f"\n\nTienes acceso a la siguiente lista de personas del proyecto:\n{contacts_str}\nSi una tarea es asignada a una persona de esta lista, debes usar su 'name' y 'email' exactos.\n"

        prompt = f"""
        Eres un asistente experto que procesa transcripciones de reuniones.
        Analiza el siguiente texto y extrae un resumen, las decisiones clave, los riesgos identificados, los acuerdos generales y las tareas accionables.
        
        PRECAUCIÓN MUY IMPORTANTE SOBRE BÚSQUEDA DE CORREOS:
        Cuando extraigas tareas, intenta identificar y extraer los correos electrónicos mencionados por los participantes en la transcripción. 
        Si alguien deletrea su correo o indica sus datos de contacto, añádelo en 'owner_email'.
        {contacts_info}
        
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
