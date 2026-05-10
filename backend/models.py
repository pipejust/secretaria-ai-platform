from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, List
from pydantic import HttpUrl
from datetime import datetime

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

    # Auto-Dispatch parametrizable por proyecto. Si `auto_dispatch_enabled` es True,
    # tras `auto_dispatch_timeout_hours` horas en estado 'pending' la sesión se
    # despacha automáticamente (correos + plataformas) sin curación humana.
    # Si `auto_dispatch_enabled` es None, se cae al setting global
    # IntegrationSetting('autoCuration').
    auto_dispatch_enabled: Optional[bool] = Field(
        default=None,
        description="Override por proyecto del Auto-Dispatch global. None = usar global.",
    )
    auto_dispatch_timeout_hours: Optional[float] = Field(
        default=None,
        description="Horas de espera antes del Auto-Dispatch. None = usar global.",
    )

    # Responsable principal del proyecto. Hace seguimiento de pendientes y
    # recibe (en futuras versiones) los reportes ejecutivos.
    owner_user_id: Optional[int] = Field(
        default=None,
        foreign_key="user.id",
        description="Usuario responsable del proyecto.",
    )

    # Sprint 01 — multi-idioma. Aplica como hint para Whisper y para
    # mantener el output del summary en el idioma original cuando el
    # usuario lo prefiere así.
    language_code: Optional[str] = Field(
        default="es",
        description="ISO-639-1 (es, en, pt, fr, de, it, ja, ko, zh, ar, hi, ...)",
    )

    templates: List["Template"] = Relationship(back_populates="project")
    routings: List["Routing"] = Relationship(back_populates="project")
    sessions: List["MeetingSession"] = Relationship(back_populates="project")
    contacts: List["ProjectContact"] = Relationship(back_populates="project")

class ProjectContact(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    name: str
    email: str
    role: str
    phone: Optional[str] = Field(default=None)
    entity: Optional[str] = Field(default=None)

    project: Optional[Project] = Relationship(back_populates="contacts")

class Template(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    name: str = Field(description="Ej. Acta de Inicio, Seguimiento Menor")
    file_path: str = Field(description="Ruta donde se almacena el template localmente o en S3")
    mapping_config: str = Field(default="[]", description="JSON array de los tags activos configurados por Drag&Drop")
    style_config: str = Field(default="{}", description="JSON con colores, fuentes y tamaños para personalizar el DOCX generado")
    
    project: Optional[Project] = Relationship(back_populates="templates")

class Routing(SQLModel, table=True):
    """Configuración de a dónde enviar las tareas de un proyecto"""
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    destination_type: str = Field(description="Ej. 'Trello', 'Azure DevOps', 'Jira'")
    destination_config: str = Field(description="Un JSON stringifiado de configuraciones (ej. ID del Board)")
    is_active: bool = Field(default=True)
    
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
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    language: str = Field(default="Desconocido", description="Idioma detectado de la transcripción")
    
    # Textos crudos provenientes de IA/API
    raw_transcript: str = Field(default="")
    raw_summary: str = Field(default="")
    processed_decisions: str = Field(default="")
    processed_risks: str = Field(default="")
    processed_agreements: str = Field(default="")
    processed_attendees: str = Field(default="")
    processed_themes: str = Field(default="")
    
    status: str = Field(default="pending", description="'pending', 'approved', 'processed'")

    # Flags de uso único de los botones de IA en la curación.
    # Se setean a True después de que el usuario los presiona la primera vez.
    ai_fields_regenerated: bool = Field(
        default=False,
        description="True si ya se ejecutó 'Sugerir Campos con IA' (OpenAI) una vez.",
    )
    ai_tasks_regenerated: bool = Field(
        default=False,
        description="True si ya se ejecutó 'Regenerar Tareas' (OpenAI) una vez.",
    )

    # Sprint 01 — Quick wins: override del LLM provider por sesión.
    # Valores: 'auto' (default: groq insights + openai tareas), 'openai', 'groq'.
    llm_provider: str = Field(
        default="auto",
        description="'auto' | 'openai' | 'groq'",
    )

    project: Optional[Project] = Relationship(back_populates="sessions")
    action_items: List["ActionItem"] = Relationship(back_populates="session")


class OutputTemplate(SQLModel, table=True):
    """Sprint 04 — plantillas para outputs role-específicos."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, description="ej. 'Deal Brief'")
    role_type: str = Field(
        index=True,
        description="commercial | product | hr | status | kickoff | eval | custom",
    )
    prompt_template: str = Field(
        description="Plantilla con placeholder {{transcript}} y opcionales {{contacts}}, {{date}}.",
    )
    output_format: str = Field(default="markdown")
    is_active: bool = Field(default=True)


class SessionOutput(SQLModel, table=True):
    """Sprint 04 — output generado para una sesión."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="meetingsession.id", index=True)
    template_id: int = Field(foreign_key="outputtemplate.id", index=True)
    title: str
    body: str
    output_format: str = Field(default="markdown")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")


class MeetingSessionVersion(SQLModel, table=True):
    """Sprint 07 — snapshot del acta antes de cada PUT del usuario."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="meetingsession.id", index=True)
    version_number: int = Field(default=1)
    snapshot_json: str = Field(description="JSON del MeetingSession completo en este punto.")
    edited_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class Comment(SQLModel, table=True):
    """Sprint 07 — comentarios sticky por sección de la sesión."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="meetingsession.id", index=True)
    section: str = Field(
        description="summary | decisions | risks | agreements | task | general",
    )
    ref_id: Optional[int] = Field(
        default=None,
        description="Para section='task': id del ActionItem.",
    )
    author_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    body: str
    parent_comment_id: Optional[int] = Field(default=None, foreign_key="comment.id")
    resolved_at: Optional[str] = Field(default=None)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class SessionPermission(SQLModel, table=True):
    """Sprint 07 — permisos granulares por sesión + usuario."""

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="meetingsession.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str = Field(default="viewer", description="viewer | editor | admin")
    granted_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CalendarAccount(SQLModel, table=True):
    """Sprint 03 — credenciales OAuth de un usuario para Google/Microsoft."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    provider: str = Field(index=True, description="'google' | 'microsoft'")
    account_email: str
    access_token: str = Field(description="Cifrar en producción; plaintext en dev.")
    refresh_token: Optional[str] = Field(default=None)
    token_expires_at: Optional[str] = Field(default=None)
    scopes: str = Field(default="")
    is_active: bool = Field(default=True)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class CalendarEvent(SQLModel, table=True):
    """Sprint 03 — evento sincronizado desde Google/Microsoft."""

    id: Optional[int] = Field(default=None, primary_key=True)
    calendar_account_id: int = Field(foreign_key="calendaraccount.id", index=True)
    external_id: str = Field(index=True, description="ID nativo del provider")
    title: str
    start_at: str
    end_at: str
    attendees_json: str = Field(default="[]")
    meeting_url: Optional[str] = Field(default=None)
    project_id: Optional[int] = Field(default=None, foreign_key="project.id")
    session_id: Optional[int] = Field(
        default=None, foreign_key="meetingsession.id",
        description="Vincula al MeetingSession cuando llega su acta.",
    )
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class AuditLog(SQLModel, table=True):
    """Sprint 08 — audit log para SOC 2 / GDPR compliance."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    action: str = Field(index=True, description="login, logout, edit_settings, delete, dispatch, ...")
    resource_type: Optional[str] = Field(default=None)
    resource_id: Optional[str] = Field(default=None)
    ip: Optional[str] = Field(default=None)
    user_agent: Optional[str] = Field(default=None)
    payload_diff: Optional[str] = Field(default=None, description="JSON con before/after.")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ApiKey(SQLModel, table=True):
    """Sprint 11 — API keys para clientes que consumen Notiva API pública."""

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    name: str = Field(description="Etiqueta humana de la key (ej. 'Zapier prod')")
    hashed_key: str = Field(unique=True, index=True)
    scopes: str = Field(default="[]", description="JSON array de scopes permitidos.")
    rate_limit_per_min: int = Field(default=60)
    last_used_at: Optional[str] = Field(default=None)
    revoked_at: Optional[str] = Field(default=None)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class EmbeddingChunk(SQLModel, table=True):
    """Chunk vectorizado del contenido de una sesión, indexado en pgvector.

    Producido por `services/embedding_service.embed_session()` al final del
    pipeline IA. La columna `embedding` se persiste vía SQL crudo (DDL en
    `_apply_lightweight_migrations`) porque SQLModel/SQLAlchemy no tiene
    tipo nativo para pgvector. Aquí la declaramos como str solo para que
    el modelo Python compile; el DDL real es VECTOR(1536).
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="meetingsession.id", index=True)
    kind: str = Field(
        description="'summary' | 'decisions' | 'risks' | 'agreements' | 'transcript'",
        index=True,
    )
    chunk_index: int = Field(default=0, description="0..N para kind='transcript'")
    content: str = Field(description="Texto original del chunk")
    embedding: Optional[str] = Field(
        default=None,
        description="VECTOR(1536) en Postgres; serializa como str en Python.",
    )
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ActionItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: int = Field(foreign_key="meetingsession.id")

    owner_name: str
    owner_email: str
    title: str
    description: str = Field(default="")
    due_date: Optional[str] = Field(default=None)

    external_id: Optional[str] = Field(
        default=None,
        description="ID en el sistema remoto ej. Jira para evitar duplicados",
    )
    is_approved: bool = Field(default=False)

    # Trazabilidad de cumplimiento. Pipeline manda 'pending'; el usuario
    # marca 'done', 'blocked' o 'cancelled' desde /admin/pendientes.
    status: str = Field(
        default="pending",
        description="'pending' | 'done' | 'blocked' | 'cancelled'",
    )
    completed_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp cuando status pasa a 'done'.",
    )

    session: Optional[MeetingSession] = Relationship(back_populates="action_items")
