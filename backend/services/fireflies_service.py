import httpx
from config import settings
from typing import Dict, Any

class FirefliesService:
    BASE_URL = "https://api.fireflies.ai/graphql"

    async def get_transcript_data(self, transcript_id: str) -> Dict[str, Any]:
        """Consulta la API de Fireflies para obtener datos detallados de una reunión."""
        query = """
        query Transcript($transcriptId: String!) {
            transcript(id: $transcriptId) {
                id
                title
                date
                summary {
                    action_items
                    overview
                }
                sentences {
                    text
                    speaker_name
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

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                response = await client.post(self.BASE_URL, json=payload, headers=headers)
                response.raise_for_status()
            except httpx.ReadTimeout:
                print(f"Fireflies API ReadTimeout fetching transcript {transcript_id}")
                raise
                
            data = response.json()
            
            # Manejo de errores de GraphQL
            if "errors" in data:
                raise Exception(f"GraphQL Error: {data['errors']}")
                
            return data["data"]["transcript"]
