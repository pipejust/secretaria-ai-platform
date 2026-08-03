from sqlmodel import SQLModel, Field, Relationship, UniqueConstraint
from typing import Optional, List
from pydantic import HttpUrl
from datetime import datetime


# ============================================================================
# Multi-tenancy
# ============================================================================
# Cada `Tenant` representa una EMPRESA cliente. Los datos están AISLADOS por
# tenant: ningún query devuelve datos de otra empresa. La plataforma sigue
# llamándose Acten (eso vive en `branding_service.DEFAULT_BRANDING`); cada
# tenant tiene su propia marca (logo, nombre comercial, colores) y sus
# propios usuarios, proyectos, sesiones, integraciones, etc.
#
# Resolución del tenant en runtime:
#   1) JWT carry: el token de un usuario lleva su tenant_id.
#   2) URL slug: `/t/{slug}/...` o `?tenant={slug}` para endpoints públicos
#      (login, branding antes de tener token).
#   3) Custom domain: `Tenant.domain` (ej. `app.empresa.com` → tenant 'empresa').
#
# Backfill: en la migración ligera creamos un tenant por defecto `acten`
# (id=1) y le asignamos todos los registros pre-existentes.

class Tenant(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(
        index=True, unique=True,
        description="Identificador URL-safe ej. 'nexura'. Se usa en /t/{slug}/.",
    )
    name: str = Field(description="Nombre legible de la empresa.")
    domain: Optional[str] = Field(
        default=None, index=True, unique=True,
        description="Dominio custom (ej. 'app.empresa.com'). Opcional.",
    )
    branding_json: str = Field(
        default="{}",
        description="JSON con company_name, logo_data_url, primary_color, etc.",
    )
    # Contenido editable del landing público (solo se usa para el tenant 'acten',
    # que es el dueño de acten.app). El admin puede editar todos los textos,
    # features, testimonios, pricing, etc. desde /admin/landing-cms. Guardado
    # como JSON para no requerir migrations cuando se añade/quita una sección.
    landing_content_json: str = Field(
        default="{}",
        description="JSON con la configuración del landing público (solo acten).",
    )
    is_active: bool = Field(default=True)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    # Idioma por defecto del tenant — se usa como fallback cuando un usuario
    # no tiene preferencia setteada. Valores soportados: es | ca | en.
    default_language: str = Field(default="es", max_length=4, description="Idioma fallback del tenant.")

    # ============================================================
    # Modelo "compartido vs per-user" para Integraciones y Routings
    # ============================================================
    # El owner del tenant (primer usuario que lo creó) decide si:
    #   - share_integrations: las credenciales de Trello/Jira/ClickUp/
    #     Azure son las suyas para TODO el equipo. Si OFF, cada usuario
    #     configura las suyas (per-user puro).
    #   - share_routings: las rutas de integración por proyecto son las
    #     suyas para TODO el equipo. Si OFF, cada usuario crea las suyas.
    # Las dos son ortogonales. Mientras está ON, los datos personales
    # de los demás se PRESERVAN (no se borran de BD) pero quedan ocultos
    # y sin uso; si el switch vuelve a OFF, reaparecen sin pérdida.
    owner_user_id: Optional[int] = Field(
        default=None,
        foreign_key="user.id",
        index=True,
        description="Dueño del tenant. Único usuario que ve los switches share_*.",
    )
    share_integrations: bool = Field(
        default=True,
        description=(
            "ON: los demás usuarios heredan las credenciales del owner. "
            "OFF: cada usuario configura las suyas (per-user)."
        ),
    )
    share_routings: bool = Field(
        default=False,
        description=(
            "ON: los demás usuarios usan las rutas del owner por proyecto. "
            "OFF: cada usuario crea las suyas (per-user)."
        ),
    )


class Role(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True, description="Nombre del rol, ej. admin, validator, viewer")
    description: str = Field(default="")
    is_active: bool = Field(default=True)
    is_system: bool = Field(default=False, description="Roles de sistema no se pueden eliminar")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    users: List["User"] = Relationship(back_populates="role")


class RolePermission(SQLModel, table=True):
    """Permisos granulares por rol — módulo + acción.

    `module_key` es la llave del módulo (ej. 'reuniones', 'proyectos').
    `action` es la operación: 'view' | 'create' | 'edit' | 'delete' | 'manage' | 'export'.
    Si una fila existe con `is_granted=True` el rol tiene ese permiso.
    """

    __table_args__ = (
        UniqueConstraint("role_id", "module_key", "action", name="uq_role_perm"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    role_id: int = Field(foreign_key="role.id", index=True)
    module_key: str = Field(index=True)
    action: str
    is_granted: bool = Field(default=True)


class RoleActivity(SQLModel, table=True):
    """Bitácora de cambios sobre un rol — para "Actividad reciente" en la UI."""

    id: Optional[int] = Field(default=None, primary_key=True)
    role_id: int = Field(foreign_key="role.id", index=True)
    action: str = Field(description="created|updated|deleted|activated|deactivated|permission_updated")
    actor_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    actor_name: str = Field(default="")
    note: str = Field(default="")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class User(SQLModel, table=True):
    # Multi-tenancy: el email ya NO es globalmente único, sólo único dentro
    # de un tenant. Mismo email puede existir en empresas distintas.
    __table_args__ = (UniqueConstraint("email", "tenant_id", name="uq_user_email_per_tenant"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    email: str = Field(index=True)
    hashed_password: str
    full_name: str
    is_active: bool = Field(default=True)
    role_id: Optional[int] = Field(default=None, foreign_key="role.id")
    # Super-admin de plataforma (puede crear/listar/borrar tenants y entrar
    # a cualquiera). Default False; sólo `admin@notiva.local` lo lleva tras
    # el seed inicial.
    is_superadmin: bool = Field(default=False)

    # Integración externa: UUID del empleado en la plataforma de Servicios.
    # Los usuarios creados por sincronización NO inician sesión en la UI de
    # Acten — existen para permisos (`X-On-Behalf-Of`) y atribución.
    external_ref: Optional[str] = Field(default=None, index=True)

    # Campos de perfil opcionales — usados por la vista de Control de Accesos.
    phone: Optional[str] = Field(default=None)
    department: Optional[str] = Field(default=None)
    position: Optional[str] = Field(default=None)
    # Tracking de cuándo se creó el usuario y cuándo fue su último login.
    created_at: Optional[str] = Field(default_factory=lambda: datetime.now().isoformat())
    last_login_at: Optional[str] = Field(default=None, index=True)

    # Campos extendidos de perfil (Mi Perfil).
    location: Optional[str] = Field(default=None, description="Ciudad / país libre")
    bio: Optional[str] = Field(default=None, description="Bio corta del usuario")
    avatar_url: Optional[str] = Field(default=None, description="URL del avatar (opcional)")
    updated_at: Optional[str] = Field(default=None, description="Última edición del perfil")

    # Preferencias de notificación granulares (canales + categorías).
    notif_email_enabled:           bool = Field(default=True)
    notif_push_enabled:            bool = Field(default=True)
    notif_meeting_reminders:       bool = Field(default=True)
    notif_task_assigned:           bool = Field(default=True)
    notif_session_processed:       bool = Field(default=True)
    notif_weekly_report:           bool = Field(default=False)
    notif_security_alerts:         bool = Field(default=True)

    # Si True, el usuario DEBE cambiar su password en el próximo login antes
    # de poder usar la plataforma. Se setea cuando un admin crea/reenvía la
    # invitación con password temporal; el primer login responde un payload
    # con must_change_password=true y el frontend redirige a /change-password.
    must_change_password: bool = Field(default=False, description="Fuerza cambio de password en el próximo login.")

    # Idioma preferido del usuario en la UI. Tres opciones soportadas:
    # 'es' (español, default), 'ca' (catalán), 'en' (inglés). El frontend
    # respeta esta preferencia y la sincroniza con localStorage.
    language: str = Field(default="es", max_length=4, description="Idioma de la UI: es | ca | en")

    # Soft delete — cuando el admin elimina un usuario desde /admin/users,
    # NO borramos la fila (rompería FKs en AuditLog, ActionItem.owner_email,
    # historial de sesiones, etc.). Marcamos `deleted_at` con un ISO
    # timestamp; el listado, los logins y los lookups por email los excluyen.
    deleted_at: Optional[str] = Field(
        default=None, index=True,
        description="ISO timestamp del soft delete. NULL = activo, valor = borrado.",
    )

    # Autenticación en dos pasos (2FA) — método email-OTP.
    two_factor_enabled: bool = Field(default=False, description="Si True, el login exige verificación adicional por email.")
    two_factor_method: Optional[str] = Field(default="email", description="'email' por ahora; reservado para TOTP futuro.")
    two_factor_code_hash: Optional[str] = Field(default=None, description="Hash del último código OTP emitido.")
    two_factor_code_expires_at: Optional[str] = Field(default=None, description="ISO-8601 de expiración del código.")
    two_factor_code_purpose: Optional[str] = Field(default=None, description="'enable' (activación inicial) | 'login' (challenge).")
    two_factor_attempts: int = Field(default=0, description="Intentos fallidos del código actual; tras 5 → invalida.")

    role: Optional[Role] = Relationship(back_populates="users")

class Project(SQLModel, table=True):
    # Project.name único por tenant (no global) — distintos clientes pueden
    # tener proyectos con el mismo nombre.
    __table_args__ = (UniqueConstraint("name", "tenant_id", name="uq_project_name_per_tenant"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(index=True, description="Nombre del proyecto, usado para mapear desde Fireflies")
    description: str = Field(default="")
    is_active: bool = Field(default=True)

    # ── Integración externa (plataforma de Servicios/RRHH) ──
    # `external_ref` = UUID del proyecto en el sistema externo, que es su
    # dueño canónico. `managed_externally` marca el proyecto como
    # read-only en la UI de Acten (se edita allá, no aquí).
    external_ref: Optional[str] = Field(default=None, index=True)
    managed_externally: bool = Field(default=False)

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
    # Integración externa: UUID del empleado en la plataforma de Servicios.
    external_ref: Optional[str] = Field(default=None, index=True)

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
    """Configuración de a dónde enviar las tareas de un proyecto, por usuario.

    Per-user dentro del proyecto: cada miembro define SUS propias rutas
    (su Trello/Jira/etc.) y solo ve las suyas. `user_id` es opcional en
    el esquema durante la migración — el backfill asigna los Routings
    legacy al primer admin del tenant; nuevos creates siempre llevan
    `user_id` del current_user.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    project_id: int = Field(foreign_key="project.id")
    user_id: Optional[int] = Field(
        default=None,
        foreign_key="user.id",
        index=True,
        description=(
            "Dueño del routing. NULL solo en filas legacy antes del backfill — "
            "los endpoints rechazan crear/usar routings sin user_id."
        ),
    )
    destination_type: str = Field(description="Ej. 'Trello', 'Azure DevOps', 'Jira'")
    destination_config: str = Field(description="Un JSON stringifiado de configuraciones (ej. ID del Board)")
    is_active: bool = Field(default=True)

    project: Optional[Project] = Relationship(back_populates="routings")


# Proveedores per-user vs per-tenant.
# Per-user: cada usuario configura SUS credenciales (plataformas personales
# de gestión + calendario OAuth). Per-tenant: globales de la empresa
# (email saliente, webhooks únicos, branding).
PER_USER_INTEGRATION_PROVIDERS: frozenset[str] = frozenset({
    "trello", "jira", "clickup", "azure",
    "google", "microsoft",
})


class IntegrationSetting(SQLModel, table=True):
    """Configuración de Integraciones — per-tenant o per-user.

    Hay dos clases de filas, distinguidas por `user_id`:

    - `user_id IS NULL`: per-tenant. Una sola por (provider, tenant).
      Provider típicos: fireflies, resend, smtp, branding, autoCuration.
      Sólo admins las pueden tocar.
    - `user_id IS NOT NULL`: per-user. Una por (provider, tenant, user).
      Provider típicos: trello, jira, clickup, azure, google, microsoft.
      Cada usuario edita SOLO la suya.

    Los UNIQUE índices parciales se crean en _apply_lightweight_migrations
    (Postgres) porque SQLModel/SQLAlchemy no expresan UNIQUE WHERE en
    `__table_args__`. El `UniqueConstraint` legacy se DROPpea durante la
    migración para no chocar con los nuevos índices.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_id: Optional[int] = Field(
        default=None,
        foreign_key="user.id",
        index=True,
        description="NULL = per-tenant; NOT NULL = per-user (dueño de las credenciales)",
    )
    provider_name: str = Field(index=True, description="Ej: fireflies, resend, azure, trello, jira, clickup")
    config_json: str = Field(default="{}", description="Configuraciones en JSON incluyendo tokens")
    is_active: bool = Field(default=True)

class MeetingSession(SQLModel, table=True):
    # fireflies_id único por tenant (no global) — distintos clientes pueden
    # usar la misma instancia de Fireflies sin colisión.
    __table_args__ = (
        UniqueConstraint("fireflies_id", "tenant_id", name="uq_meetingsession_ff_per_tenant"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    fireflies_id: str = Field(index=True)
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

    # Salud del pipeline IA (Sprint Estabilidad — fix 'procesos a medias').
    # Cuando el webhook llega y termina el pipeline IA, estos campos cuentan
    # qué se logró y qué quedó pendiente. Si processing_error != "", la
    # sesión NO está completa y debe reintentarse — manualmente desde la UI
    # o automáticamente por el cron de retry.
    processing_error: str = Field(
        default="",
        description="Mensaje del último error en el pipeline IA. Vacío = OK.",
    )
    processing_attempts: int = Field(
        default=0,
        description="Cuántas veces se ha ejecutado el pipeline IA completo.",
    )
    processing_completed_at: str = Field(
        default="",
        description="ISO timestamp cuando el pipeline terminó SIN errores.",
    )

    # Auto-dispatch — bloqueo y notificaciones.
    # Cuando el cron de auto-curación llega al timeout pero detecta que faltan
    # correos en tareas o participantes, NO despacha y deja registrado aquí
    # el ISO timestamp del último warning enviado al admin. La siguiente
    # iteración del cron lo lee para NO re-spamear (re-envío sólo después de
    # WARNING_REPEAT_HOURS, hoy 24h).
    auto_dispatch_warning_at: str = Field(
        default="",
        description="ISO ts del último correo de 'no se puede auto-despachar' enviado.",
    )
    # Razón legible del bloqueo (para UI + emails). Vacío = sin bloqueo activo.
    # Valores típicos: 'missing_task_emails', 'missing_participants', 'pipeline_error'.
    auto_dispatch_blocked_reason: str = Field(
        default="",
        description="Razón legible por la cual auto-dispatch no se ejecutó.",
    )
    # ISO ts cuando se envió el correo "sesión procesada" (post-pipeline).
    # Sirve para que el correo NO se reenvíe en cada retry del pipeline.
    session_ready_email_sent_at: str = Field(
        default="",
        description="ISO ts del correo 'sesión procesada y lista' enviado al admin.",
    )

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
    """Sprint 04 — plantillas para outputs role-específicos. Per-tenant."""

    __table_args__ = (
        UniqueConstraint("name", "tenant_id", name="uq_outputtemplate_name_per_tenant"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(index=True, description="ej. 'Deal Brief'")
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


class PersonAlias(SQLModel, table=True):
    """Otra forma de escribir el nombre de alguien que ya conocemos.

    La transcripción oye lo que oye: la misma persona sale como «Juan
    Diego Toro», «JDiego Toro», «Juan Toro» y «TON618 Toro» —su apodo de
    la videollamada— y cada variante abría su propio carril en el
    tablero y su propia fila en los informes.

    Se guarda en base y no en el código para que corregir una grafía
    nueva no exija un despliegue: aparecen constantemente.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    # Nombre tal como aparece, normalizado (sin tildes, en minúsculas).
    alias: str = Field(index=True)
    canonical_name: str
    canonical_email: str = Field(default="")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class IntegrationPairing(SQLModel, table=True):
    """Código de emparejamiento de un solo uso para conectar una plataforma.

    Existe para que **nadie tenga que manejar una clave a mano**. Copiar
    una cadena de cincuenta caracteres de un chat a un formulario acaba
    con la clave en el historial de alguien, o pegada mal y con un cero
    donde había una o. Aquí el humano copia un código corto y de vida
    breve; la clave de verdad viaja de servidor a servidor y no la ve
    ninguna persona.

    Un solo uso y quince minutos: si se filtra, ya se ha canjeado o ya ha
    caducado. Se guarda el hash, no el código, por la misma razón que las
    claves.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    code_hash: str = Field(index=True, description="sha256 del código")
    scopes: str = Field(default="[]", description="JSON array de alcances a conceder")
    created_by_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    expires_at: str
    redeemed_at: Optional[str] = Field(default=None)
    redeemed_by: Optional[str] = Field(
        default=None, description="Nombre que declaró la plataforma al canjear",
    )
    api_key_id: Optional[int] = Field(
        default=None, description="Clave emitida en el canje.",
    )
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class OutboundIntegration(SQLModel, table=True):
    """A dónde manda Acten sus eventos, por empresa.

    Antes esto vivía en variables de entorno, lo que ataba el despliegue
    entero a **una** plataforma conectada. Al estar en base, cada empresa
    apunta a la suya y el emparejamiento puede configurarlo solo.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    name: str = Field(default="", description="Nombre de la plataforma conectada")
    webhook_url: str = Field(default="")
    # Lo fija quien verifica: ellos reciben, así que el secreto es suyo.
    webhook_secret: str = Field(default="")
    # Clave con la que Acten llama a SU API (la dirección contraria).
    remote_api_key: str = Field(default="")
    remote_base_url: str = Field(default="")
    is_active: bool = Field(default=True)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: Optional[str] = Field(default=None)


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
    """Sprint 08 — audit log para SOC 2 / GDPR compliance. Per-tenant."""

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: Optional[int] = Field(default=None, foreign_key="tenant.id", index=True)
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    action: str = Field(index=True, description="login, logout, edit_settings, delete, dispatch, ...")
    resource_type: Optional[str] = Field(default=None)
    resource_id: Optional[str] = Field(default=None)
    ip: Optional[str] = Field(default=None)
    user_agent: Optional[str] = Field(default=None)
    payload_diff: Optional[str] = Field(default=None, description="JSON con before/after.")
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())


class ApiKey(SQLModel, table=True):
    """Sprint 11 — API keys para clientes que consumen la API pública."""

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
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
    # Denormalizado para queries directos sin join al meetingsession.
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    session_id: int = Field(foreign_key="meetingsession.id")

    owner_name: str
    owner_email: str
    title: str
    description: str = Field(default="")
    due_date: Optional[str] = Field(default=None)
    # Hora del compromiso (HH:MM 24h) — la separamos del due_date para
    # que el filtro y el calendario puedan combinar fecha + hora sin
    # ambigüedad. Opcional: si la reunión no estableció hora, queda null.
    due_time: Optional[str] = Field(
        default=None, max_length=5,
        description="Hora límite en formato 'HH:MM' (24h). Opcional.",
    )

    # Prioridad explícita. La generación por IA la infiere del contexto;
    # el usuario puede ajustarla. Default 'media'.
    priority: str = Field(
        default="media", max_length=10,
        description="'alta' | 'media' | 'baja'",
    )

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

    # ── Integración externa + Kanban ──
    origin: str = Field(
        default="meeting",
        description="'meeting' (nació de una reunión) | 'manual' (creada por un humano)",
    )
    kanban_column: Optional[str] = Field(
        default=None, description="Columna del tablero Kanban. Opcional."
    )
    kanban_order: Optional[int] = Field(
        default=None, description="Posición dentro de la columna. Opcional."
    )
    updated_at: Optional[str] = Field(
        default=None,
        description="ISO timestamp de la última modificación — habilita ?updated_since=",
    )

    session: Optional[MeetingSession] = Relationship(back_populates="action_items")


# ============================================================================
# Notifications — feed in-app por usuario, con deep-link al recurso.
# Multi-tenant: tenant_id denormalizado para filtrar sin join al user.
# ============================================================================
class Notification(SQLModel, table=True):
    """Notificación in-app dirigida a un usuario específico.

    Diseño:
    - Cada notif pertenece a un (tenant_id, user_id) — el bell del topbar
      sólo muestra las del usuario logueado y de su tenant activo.
    - `kind` clasifica el origen (session_processed | session_received |
      task_assigned | routing_failed | comment_mention | etc) para filtrar
      por tipo y para elegir el icono en el frontend.
    - `link_to` es la ruta interna a la que el frontend navega al hacer
      click — ej. '/admin/curation/123' o '/admin/projects/4'. La validación
      de la ruta es cosmética; el navegador la resuelve al click.
    - `entity_type` + `entity_id` son metadata útil para deduplicar y para
      evitar emitir N notificaciones idénticas a la misma persona.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    # Destinatario. None = broadcast a todo el tenant (no usado hoy, pero
    # dejamos la puerta abierta).
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)

    kind: str = Field(
        index=True,
        description=(
            "Categoría del evento: 'session_processed' | 'session_received' | "
            "'task_assigned' | 'routing_failed' | 'comment_mention' | 'system'."
        ),
    )
    title: str = Field(max_length=240, description="Una línea — qué pasó.")
    body: str = Field(default="", max_length=600, description="Detalle opcional.")

    # Deep-link interno. Cuando el user hace click, el frontend navega aquí.
    link_to: Optional[str] = Field(default=None, max_length=240)
    # Metadata para futura deduplicación / agrupación.
    entity_type: Optional[str] = Field(default=None, max_length=32, index=True)
    entity_id: Optional[int] = Field(default=None, index=True)

    is_read: bool = Field(default=False, index=True)
    read_at: Optional[str] = Field(default=None)
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), index=True)


# ============================================================================
# Historial de Pregunta a Acten (RAG)
# ============================================================================
# Cada vez que un usuario hace una pregunta vía POST /api/ask, persistimos
# pregunta + respuesta para que el botón "Historial" del componente Ask sea
# durable. El historial es POR USUARIO (no compartido en el tenant).
# `structured` y `citations` se guardan como JSON serializado para no
# inflar el modelo con campos N-cardinalidad.

class AskHistory(SQLModel, table=True):
    """Una entrada del historial RAG. 1 fila = 1 turn (pregunta + respuesta)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)

    # Pregunta original del usuario (preservamos el texto tal cual).
    question: str = Field(max_length=4000)
    # Respuesta en markdown (lo que se muestra como answer fallback).
    answer: str = Field(default="")
    # Estructura cruda devuelta por el LLM serializada como JSON string —
    # `intro`, `decisions[]`, `action_items[]`. Guardamos texto para no
    # casarnos con un esquema rígido a nivel de DB.
    structured_json: Optional[str] = Field(default=None)
    # Lista de citations (JSON string). Cada citation = {session_id, kind,
    # snippet, distance}.
    citations_json: Optional[str] = Field(default=None)

    # Filtro aplicado en la consulta (útil para mostrar contexto al re-abrir).
    project_id: Optional[int] = Field(default=None, foreign_key="project.id", index=True)

    # Metadata de la respuesta para mostrar confianza/modelo en el historial.
    model: str = Field(default="", max_length=80)
    chunks_used: int = Field(default=0)

    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), index=True)


# ============================================================================
# Mensajes del formulario de contacto del landing público.
# Cada submission del form de acten.app se persiste para que el admin
# pueda verlos en la UI y reciba una notificación in-app.
# ============================================================================

class ContactMessage(SQLModel, table=True):
    """Mensaje enviado desde el formulario público del landing.

    Multi-tenant: tenant_id denormalizado. Hoy solo el tenant 'acten'
    recibe estos (porque acten.app es el único landing público), pero el
    modelo está listo para que cualquier tenant white-label tenga lo suyo.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: int = Field(foreign_key="tenant.id", index=True)

    # Campos llenados por el visitante
    name: str = Field(max_length=120)
    email: str = Field(max_length=240, index=True)
    company: Optional[str] = Field(default=None, max_length=120)
    role: Optional[str] = Field(default=None, max_length=120)
    message: str = Field(max_length=4000)

    # Metadata operacional
    ip: Optional[str] = Field(default=None, max_length=64)
    user_agent: Optional[str] = Field(default=None, max_length=500)
    referer: Optional[str] = Field(default=None, max_length=500)

    # Estado para que el admin marque qué ya atendió
    status: str = Field(
        default="new", max_length=16, index=True,
        description="'new' | 'read' | 'replied' | 'archived'",
    )
    read_at: Optional[str] = Field(default=None)
    replied_at: Optional[str] = Field(default=None)

    # Si el envío del email notificando al destinatario falló o el email
    # bounceó, guardamos la razón para debug futuro.
    email_status: str = Field(
        default="pending", max_length=32,
        description="'pending' | 'sent' | 'failed'",
    )
    email_error: str = Field(default="", max_length=400)

    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(), index=True)
