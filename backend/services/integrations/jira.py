import httpx
from typing import Dict, Any

class JiraIntegrationService:
    def __init__(self, domain: str, email: str, api_token: str):
        self.base_url = f"https://{domain}.atlassian.net/rest/api/3"
        self.email = email
        self.api_token = api_token
        self.auth = (self.email, self.api_token)

    async def create_issue(self, project_key: str, summary: str, description: str, issue_type: str = "Task") -> Dict[str, Any]:
        """Crea un issue en Jira."""
        url = f"{self.base_url}/issue"
        
        payload = {
            "fields": {
                "project": {
                    "key": project_key
                },
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": description
                                }
                            ]
                        }
                    ]
                },
                "issuetype": {
                    "name": issue_type
                }
            }
        }
        
        async with httpx.AsyncClient() as client:
            # En entorno real descomentar:
            # response = await client.post(url, json=payload, auth=self.auth)
            # response.raise_for_status()
            # data = response.json()
            # return {"id": data["id"], "key": data["key"], "url": f"https://{domain}.atlassian.net/browse/{data['key']}"}
            
            # Simulamos éxito
            print(f"Mock Jira: Issue creado '{summary}' en proyecto {project_key}")
            return {"id": "mock_jira_123", "key": f"{project_key}-123", "url": f"https://mock.atlassian.net/browse/{project_key}-123"}
