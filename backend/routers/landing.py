from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(
    prefix="/api/v1",
    tags=["Landing"]
)

class LandingContent(BaseModel):
    title: str
    description: str
    features: list[str]

@router.get("/landing_page_content", response_model=LandingContent)
def get_landing_content():
    return {
        "title": "Bienvenido a Secretaría AI MoshWasi",
        "description": "Orquestador principal para procesamiento de actas y tareas. Todo tu trabajo documental en un solo lugar.",
        "features": [
            "Transcripción Automática",
            "Gestión de Proyectos",
            "Integraciones con Trello, Jira y ClickUp"
        ]
    }
