import httpx
from typing import Dict, Any, Optional
import time
from datetime import datetime

class ClickUpIntegrationService:
    BASE_URL = "https://api.clickup.com/api/v2"

    def __init__(self, api_token: str):
        self.api_token = api_token
        self.headers = {
            "Authorization": self.api_token,
            "Content-Type": "application/json"
        }

    async def get_member_id_by_email(self, list_id: str, email: str, client: httpx.AsyncClient) -> Optional[int]:
        url = f"{self.BASE_URL}/list/{list_id}/member"
        try:
            response = await client.get(url, headers=self.headers)
            if response.status_code == 200:
                data = response.json()
                for member in data.get("members", []):
                    if member.get("email", "").lower() == email.lower():
                        return member.get("id")
        except Exception as e:
            print(f"Error fetching ClickUp members: {e}")
        return None

    async def create_task(self, list_id: str, name: str, description: str, due_date: str = None, owner_email: str = None) -> Dict[str, Any]:
        """Crea una tarea en ClickUp."""
        url = f"{self.BASE_URL}/list/{list_id}/task"
        
        start_date_ms = int(time.time() * 1000)
        due_date_ms = None
        
        if due_date:
            try:
                # Tratar de parsear formato ISO (ej: 2024-03-16 o 2024-03-16T12:00:00Z)
                clean_date = due_date.replace("Z", "+00:00")
                if len(clean_date) == 10: # Solo YYYY-MM-DD
                    dt = datetime.strptime(clean_date, "%Y-%m-%d")
                else:
                    dt = datetime.fromisoformat(clean_date)
                due_date_ms = int(dt.timestamp() * 1000)
            except Exception as e:
                print(f"Error parsing ClickUp due date {due_date}: {e}")
        
        payload = {
            "name": name,
            "markdown_description": description,
            "start_date": start_date_ms
        }
        
        if due_date_ms:
            payload["due_date"] = due_date_ms
            
        async with httpx.AsyncClient() as client:
            if owner_email:
                member_id = await self.get_member_id_by_email(list_id, owner_email, client)
                if member_id:
                    payload["assignees"] = [member_id]
            
            response = await client.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            return {"id": data["id"], "url": data["url"]}
