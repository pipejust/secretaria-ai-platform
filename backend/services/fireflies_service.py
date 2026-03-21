import httpx
from config import settings
from typing import Dict, Any

class FirefliesService:
    BASE_URL = "https://api.fireflies.ai/graphql"

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
            "Authorization": f"Bearer {settings.fireflies_api_key}",
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
