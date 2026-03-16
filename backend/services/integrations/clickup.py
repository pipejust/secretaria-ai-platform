import httpx
from typing import Dict, Any

class ClickUpIntegrationService:
    BASE_URL = "https://api.clickup.com/api/v2"

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json"
        }

    async def create_task(self, list_id: str, name: str, description: str, due_date: int = None) -> Dict[str, Any]:
        """Crea una tarea en ClickUp."""
        url = f"{self.BASE_URL}/list/{list_id}/task"
        
        payload = {
            "name": name,
            "markdown_description": description,
            "due_date": due_date
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            return {"id": data["id"], "url": data["url"]}
