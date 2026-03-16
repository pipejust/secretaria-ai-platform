import httpx
from typing import Dict, Any, Optional

class JiraIntegrationService:
    def __init__(self, domain: str, email: str, api_token: str):
        self.domain = domain.replace('.atlassian.net', '').strip()
        self.base_url = f"https://{self.domain}.atlassian.net/rest/api/3"
        self.email = email
        self.api_token = api_token
        self.auth = (self.email, self.api_token)

    async def get_account_id_by_email(self, email: str, client: httpx.AsyncClient) -> Optional[str]:
        url = f"{self.base_url}/user/search"
        params = {"query": email}
        try:
            response = await client.get(url, params=params, auth=self.auth)
            if response.status_code == 200:
                data = response.json()
                if data and len(data) > 0:
                    return data[0].get("accountId")
        except Exception as e:
            print(f"Error fetching Jira user by email: {e}")
        return None

    async def create_issue(self, project_key: str, summary: str, description: str, issue_type: str = "Task", due_date: str = None, owner_email: str = None) -> Dict[str, Any]:
        """Crea un issue en Jira."""
        url = f"{self.base_url}/issue"
        
        content_nodes = []
        for line in description.split('\n'):
            line = line.strip()
            if line:
                content_nodes.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": line}]
                })
            else:
                # Empty line as spacer
                content_nodes.append({
                    "type": "paragraph",
                    "content": []
                })
        
        payload = {
            "fields": {
                "project": {
                    "key": project_key
                },
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": content_nodes
                },
                "issuetype": {
                    "name": issue_type
                }
            }
        }
        
        if due_date:
            try:
                payload["fields"]["duedate"] = due_date[:10]
            except Exception:
                pass
        
        async with httpx.AsyncClient() as client:
            if owner_email:
                account_id = await self.get_account_id_by_email(owner_email, client)
                if account_id:
                    payload["fields"]["assignee"] = {"accountId": account_id}
                    
            response = await client.post(url, json=payload, auth=self.auth)
            try:
                response.raise_for_status()
                data = response.json()
                return {"id": data["id"], "key": data["key"], "url": f"https://{self.domain}.atlassian.net/browse/{data['key']}"}
            except httpx.HTTPStatusError as e:
                print(f"Jira API error on strict create_issue: {e.response.text}")
                
                # Fallback: Remover campos problemáticos (assignee, duedate) que suelen fallar por permisos de pantalla en Jira
                if "assignee" in payload["fields"]:
                    del payload["fields"]["assignee"]
                if "duedate" in payload["fields"]:
                    del payload["fields"]["duedate"]
                    
                print("Retrying Jira without assignee/duedate...")
                fallback_resp = await client.post(url, json=payload, auth=self.auth)
                fallback_resp.raise_for_status()
                data = fallback_resp.json()
                return {"id": data["id"], "key": data["key"], "url": f"https://{self.domain}.atlassian.net/browse/{data['key']}"}
