import httpx
from typing import Dict, Any

class TrelloIntegrationService:
    BASE_URL = "https://api.trello.com/1"
    
    def __init__(self, api_key: str, token: str):
        self.api_key = api_key
        self.token = token
        
    async def create_card(self, board_id: str, list_id: str, title: str, description: str, due_date: str) -> Dict[str, Any]:
        """Crea una tarjeta en Trello."""
        url = f"{self.BASE_URL}/cards"
        query = {
            'key': self.api_key,
            'token': self.token,
            'idList': list_id,
            'name': title,
            'desc': description,
            'due': due_date
        }
        
        async with httpx.AsyncClient() as client:
            # En entorno real descomentar:
            # response = await client.post(url, params=query)
            # response.raise_for_status()
            # return response.json()
            
            # Simulamos éxito
            print(f"Mock Trello: Tarjeta creada '{title}' en list {list_id}")
            return {"id": "mock_trello_card_123", "url": "https://trello.com/c/mock"}
