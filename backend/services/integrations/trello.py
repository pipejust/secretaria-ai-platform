import httpx
from typing import Dict, Any, Optional

class TrelloIntegrationService:
    BASE_URL = "https://api.trello.com/1"
    
    def __init__(self, api_key: str, token: str):
        self.api_key = api_key
        self.token = token
        
    async def get_member_id_by_email(self, email: str, client: httpx.AsyncClient) -> Optional[str]:
        url = f"{self.BASE_URL}/search/members"
        params = {
            'key': self.api_key,
            'token': self.token,
            'query': email,
            'limit': 1
        }
        try:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                data = response.json()
                if data and isinstance(data, list) and len(data) > 0:
                    return data[0].get("id")
        except Exception as e:
            print(f"Error fetching Trello member by email: {e}")
        return None

    async def create_card(self, board_id: str, list_id: str, title: str, description: str, due_date: str = None, owner_email: str = None) -> Dict[str, Any]:
        """Crea una tarjeta en Trello."""
        url = f"{self.BASE_URL}/cards"
        query = {
            'key': self.api_key,
            'token': self.token,
            'idList': list_id,
            'name': title,
            'desc': description
        }
        
        if due_date:
            query['due'] = due_date
            
        async with httpx.AsyncClient() as client:
            if owner_email:
                member_id = await self.get_member_id_by_email(owner_email, client)
                if member_id:
                    query['idMembers'] = member_id
                    
            response = await client.post(url, params=query)
            response.raise_for_status()
            return response.json()
