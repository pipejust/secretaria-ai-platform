import httpx
from typing import Dict, Any

class AzureDevOpsIntegrationService:
    def __init__(self, organization: str, project: str, pat: str):
        self.organization = organization
        self.project_name = project
        self.pat = pat
        self.base_url = f"https://dev.azure.com/{organization}/{project}/_apis/wit/workitems"

    async def create_work_item(self, title: str, description: str, wi_type: str = "Task", assigned_to: str = "") -> Dict[str, Any]:
        """Crea un Work Item en Azure DevOps."""
        url = f"{self.base_url}/${wi_type}?api-version=7.0"
        
        # El body en ADO requiere formato JSON Patch
        payload = [
            {
                "op": "add",
                "path": "/fields/System.Title",
                "value": title
            },
            {
                "op": "add",
                "path": "/fields/System.Description",
                "value": description
            }
        ]
        
        if assigned_to:
            payload.append({
                "op": "add",
                "path": "/fields/System.AssignedTo",
                "value": assigned_to
            })

        auth = ("", self.pat)
        headers = {"Content-Type": "application/json-patch+json"}
        
        async with httpx.AsyncClient() as client:
            # En producción:
            # response = await client.post(url, json=payload, auth=auth, headers=headers)
            # response.raise_for_status()
            # return response.json()
            
            # Simulamos éxito
            print(f"Mock ADO: Work Item '{title}' creado para {assigned_to}")
            return {"id": "mock_ado_123", "url": "https://dev.azure.com/mock"}
