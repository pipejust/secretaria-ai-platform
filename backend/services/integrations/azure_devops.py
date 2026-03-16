import httpx
from typing import Dict, Any
from datetime import datetime, timezone

class AzureDevOpsIntegrationService:
    def __init__(self, organization: str, project: str, pat: str):
        self.organization = organization
        self.project_name = project
        self.pat = pat
        self.base_url = f"https://dev.azure.com/{organization}/{project}/_apis/wit/workitems"

    async def create_work_item(self, title: str, description: str, wi_type: str = "Task", assigned_to: str = "", due_date: str = None, owner_email: str = None) -> Dict[str, Any]:
        """Crea un Work Item en Azure DevOps."""
        url = f"{self.base_url}/${wi_type}?api-version=7.0"
        
        # Format the fallback description
        final_desc = description
        final_desc += f"<br><br><b>Metadatos de Secretaría:</b>"
        if owner_email:
            final_desc += f"<br>Asignado Original: {owner_email}"
        
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        final_desc += f"<br>Fecha de Inicio: {now_iso[:10]}"
        if due_date:
            final_desc += f"<br>Fecha de Vencimiento: {due_date}"
            
        base_payload = [
            {
                "op": "add",
                "path": "/fields/System.Title",
                "value": title
            },
            {
                "op": "add",
                "path": "/fields/System.Description",
                "value": final_desc
            }
        ]
        
        strict_payload = list(base_payload)
        
        # ADO Assignment
        target_email = owner_email if owner_email else assigned_to
        if target_email:
            strict_payload.append({
                "op": "add",
                "path": "/fields/System.AssignedTo",
                "value": target_email
            })

        # ADO Dates (Attempt to set standard Agile/Scrum task dates)
        strict_payload.append({
            "op": "add",
            "path": "/fields/Microsoft.VSTS.Scheduling.StartDate",
            "value": now_iso
        })
        
        if due_date:
            strict_payload.append({
                "op": "add",
                "path": "/fields/Microsoft.VSTS.Scheduling.TargetDate",
                "value": f"{due_date}T12:00:00Z"
            })

        auth = ("", self.pat)
        headers = {"Content-Type": "application/json-patch+json"}
        
        async with httpx.AsyncClient() as client:
            try:
                # Intento 1: Con todos los campos estrictos
                response = await client.post(url, json=strict_payload, auth=auth, headers=headers)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                print(f"ADO Strict payload failed ({e.response.status_code}): {e.response.text}. Retrying with safe payload.")
                # Intento 2: Fallback (solo título y descripción)
                fallback = await client.post(url, json=base_payload, auth=auth, headers=headers)
                fallback.raise_for_status()
                return fallback.json()
