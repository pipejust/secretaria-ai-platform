from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from pydantic import HttpUrl

class Role(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, description="Nombre del rol, ej. admin, validator, viewer")
    description: str = Field(default="")
    is_active: bool = Field(default=True)
    
    users: List["User"] = Relationship(back_populates="role")

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    hashed_password: str
    full_name: str
    is_active: bool = Field(default=True)
    role_id: Optional[int] = Field(default=None, foreign_key="role.id")
    
    role: Optional[Role] = Relationship(back_populates="users")

class Project(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, description="Nombre del proyecto, usado para mapear desde Fireflies")
    description: str = Field(default="")
    is_active: bool = Field(default=True)
    
    templates: List["Template"] = Relationship(back_populates="project")
    routings: List["Routing"] = Relationship(back_populates="project")
    sessions: List["MeetingSession"] = Relationship(back_populates="project")

class Template(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    name: str = Field(description="Ej. Acta de Inicio, Seguimiento Menor")
    file_path: str = Field(description="Ruta donde se almacena el template localmente o en S3")
    mapping_config: str = Field(default="[]", description="JSON array de los tags activos configurados por Drag&Drop")
    
    project: Optional[Project] = Relationship(back_populates="templates")

class Routing(SQLModel, table=True):
    """Configuración de a dónde enviar las tareas de un proyecto"""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    destination_type: str = Field(description="Ej. 'Trello', 'Azure DevOps', 'Jira'")
    destination_config: str = Field(description="Un JSON stringifiado de configuraciones (ej. ID del Board)")
    
    project: Optional[Project] = Relationship(back_populates="routings")

class IntegrationSetting(SQLModel, table=True):
    """Configuración Global de Integraciones y API Keys (SMTP, Fireflies, etc)"""
    id: Optional[int] = Field(default=None, primary_key=True)
    provider_name: str = Field(index=True, unique=True, description="Ej: fireflies, resend, azure, trello, jira, clickup")
    config_json: str = Field(default="{}", description="Configuraciones en JSON incluyendo tokens")
    is_active: bool = Field(default=True)

class MeetingSession(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    fireflies_id: str = Field(index=True, unique=True)
    title: str
    date: str
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    
    # Textos crudos provenientes de IA/API
    raw_transcript: str = Field(default="")
    raw_summary: str = Field(default="")
    processed_decisions: str = Field(default="")
    processed_risks: str = Field(default="")
    processed_agreements: str = Field(default="")
    
    status: str = Field(default="pending", description="'pending', 'approved', 'processed'")
    
    project: Optional[Project] = Relationship(back_populates="sessions")
    action_items: List["ActionItem"] = Relationship(back_populates="session")

class ActionItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="meetingsession.id")
    
    owner_name: str
    owner_email: str
    title: str
    description: str = Field(default="")
    due_date: Optional[str] = Field(default=None)
    
    external_id: Optional[str] = Field(default=None, description="ID en el sistema remoto ej. Jira para evitar duplicados")
    is_approved: bool = Field(default=False)
    
    session: Optional[MeetingSession] = Relationship(back_populates="action_items")
