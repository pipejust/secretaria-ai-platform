"""Conexión a la base de datos.

Soporta cualquier URL Postgres (Render, Supabase, AWS RDS, etc.) o SQLite
para desarrollo local. Normaliza el prefijo `postgres://` (que entrega
Render) a `postgresql+psycopg2://` (que exige SQLAlchemy 2.x).
"""

import logging

from sqlmodel import Session, SQLModel, create_engine

from config import settings

logger = logging.getLogger(__name__)


def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


_db_url = _normalize_db_url(settings.database_url)
_is_sqlite = _db_url.startswith("sqlite")

# pool_size/max_overflow no aplican a SQLite. Para Postgres mantenemos
# pool_pre_ping para detectar conexiones muertas (Render reinicia su
# Postgres ocasionalmente para mantenimiento).
engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if not _is_sqlite:
    engine_kwargs.update({"pool_size": 10, "max_overflow": 20})

engine = create_engine(_db_url, **engine_kwargs)


def create_db_and_tables() -> None:
    """Crea las tablas si no existen y aplica migraciones idempotentes ligeras.

    SQLModel/SQLAlchemy `create_all` solo crea tablas faltantes; nunca toca
    columnas. Para añadir columnas nuevas a tablas ya existentes en producción
    sin tener Alembic, ejecutamos `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    aquí mismo. Es idempotente y barato.
    """
    SQLModel.metadata.create_all(engine)
    _apply_lightweight_migrations()


DEFAULT_TENANT_SLUG = "acten"
DEFAULT_TENANT_NAME = "Acten"


def _apply_lightweight_migrations() -> None:
    """ALTER TABLE idempotentes para columnas añadidas después del schema inicial."""
    if _is_sqlite:
        statements = [
            'ALTER TABLE meetingsession ADD COLUMN ai_fields_regenerated BOOLEAN DEFAULT 0 NOT NULL',
            'ALTER TABLE meetingsession ADD COLUMN ai_tasks_regenerated  BOOLEAN DEFAULT 0 NOT NULL',
            "ALTER TABLE meetingsession ADD COLUMN processing_error TEXT NOT NULL DEFAULT ''",
            'ALTER TABLE meetingsession ADD COLUMN processing_attempts INTEGER NOT NULL DEFAULT 0',
            "ALTER TABLE meetingsession ADD COLUMN processing_completed_at TEXT NOT NULL DEFAULT ''",
            # Auto-dispatch — bloqueo y dedupe del warning
            "ALTER TABLE meetingsession ADD COLUMN auto_dispatch_warning_at TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE meetingsession ADD COLUMN auto_dispatch_blocked_reason TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE meetingsession ADD COLUMN session_ready_email_sent_at TEXT NOT NULL DEFAULT ''",
            'ALTER TABLE project ADD COLUMN auto_dispatch_enabled BOOLEAN',
            'ALTER TABLE project ADD COLUMN auto_dispatch_timeout_hours REAL',
            'ALTER TABLE project ADD COLUMN owner_user_id INTEGER REFERENCES "user"(id)',
            'ALTER TABLE actionitem ADD COLUMN status TEXT DEFAULT "pending" NOT NULL',
            'ALTER TABLE actionitem ADD COLUMN completed_at TEXT',
            # Sprint 00 — embeddings (sqlite no soporta pgvector, fallback a TEXT)
            'ALTER TABLE embeddingchunk ADD COLUMN embedding_vector TEXT',
            # Perfil + tracking en user (vista Control de Accesos).
            'ALTER TABLE "user" ADD COLUMN phone TEXT',
            'ALTER TABLE "user" ADD COLUMN department TEXT',
            'ALTER TABLE "user" ADD COLUMN position TEXT',
            'ALTER TABLE "user" ADD COLUMN created_at TEXT',
            'ALTER TABLE "user" ADD COLUMN last_login_at TEXT',
            'ALTER TABLE role ADD COLUMN is_system  INTEGER NOT NULL DEFAULT 0',
            'ALTER TABLE role ADD COLUMN created_at TEXT',
            'ALTER TABLE role ADD COLUMN updated_at TEXT',
            # Landing CMS (sqlite dev)
            "ALTER TABLE tenant ADD COLUMN landing_content_json TEXT NOT NULL DEFAULT '{}'",
        ]
    else:
        statements = [
            'ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS ai_fields_regenerated BOOLEAN DEFAULT FALSE NOT NULL',
            'ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS ai_tasks_regenerated  BOOLEAN DEFAULT FALSE NOT NULL',
            # Salud del pipeline IA — Sprint Estabilidad
            "ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS processing_error TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS processing_attempts INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS processing_completed_at VARCHAR(64) NOT NULL DEFAULT ''",
            "CREATE INDEX IF NOT EXISTS idx_meetingsession_proc_error ON meetingsession((CASE WHEN processing_error = '' THEN 0 ELSE 1 END))",
            # Auto-dispatch — bloqueo + dedupe del warning + ts del correo
            # post-pipeline para no reenviarlo en cada retry.
            "ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS auto_dispatch_warning_at VARCHAR(64) NOT NULL DEFAULT ''",
            "ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS auto_dispatch_blocked_reason VARCHAR(64) NOT NULL DEFAULT ''",
            "ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS session_ready_email_sent_at VARCHAR(64) NOT NULL DEFAULT ''",
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS auto_dispatch_enabled BOOLEAN',
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS auto_dispatch_timeout_hours DOUBLE PRECISION',
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS owner_user_id INTEGER REFERENCES "user"(id)',
            'ALTER TABLE actionitem ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT \'pending\'',
            'ALTER TABLE actionitem ADD COLUMN IF NOT EXISTS completed_at VARCHAR(64)',
            # Nuevas columnas de Tareas (priority + due_time):
            'ALTER TABLE actionitem ADD COLUMN IF NOT EXISTS priority VARCHAR(10) NOT NULL DEFAULT \'media\'',
            'ALTER TABLE actionitem ADD COLUMN IF NOT EXISTS due_time VARCHAR(5)',
            'CREATE INDEX IF NOT EXISTS idx_actionitem_priority ON actionitem(priority)',
            # Sprint 00 — pgvector (extensión + columna VECTOR(1536) reemplaza la "embedding TEXT" genérica)
            'CREATE EXTENSION IF NOT EXISTS vector',
            'ALTER TABLE embeddingchunk ADD COLUMN IF NOT EXISTS embedding_vector vector(1536)',
            'CREATE INDEX IF NOT EXISTS idx_embeddingchunk_session ON embeddingchunk(session_id)',
            'CREATE INDEX IF NOT EXISTS idx_embeddingchunk_kind    ON embeddingchunk(kind)',
            # Índice IVFFlat para búsqueda aproximada (creado vacío; primer reindex llena listas)
            "CREATE INDEX IF NOT EXISTS idx_embeddingchunk_vector ON embeddingchunk USING ivfflat (embedding_vector vector_cosine_ops) WITH (lists=100)",
            # Sprint 01 — quick wins
            "ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS llm_provider VARCHAR(16) NOT NULL DEFAULT 'auto'",
            "ALTER TABLE project        ADD COLUMN IF NOT EXISTS language_code VARCHAR(8) DEFAULT 'es'",
            # Sprints 04/07/08/11 — tablas nuevas creadas por SQLModel.metadata.create_all
            # arriba; aquí solo añadimos índices que SQLModel no genera por sí solo.
            "CREATE INDEX IF NOT EXISTS idx_outputtemplate_role     ON outputtemplate(role_type)",
            "CREATE INDEX IF NOT EXISTS idx_sessionoutput_session   ON sessionoutput(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_msv_session              ON meetingsessionversion(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_comment_session          ON comment(session_id)",
            "CREATE INDEX IF NOT EXISTS idx_sessionperm_user_session ON sessionpermission(user_id, session_id)",
            "CREATE INDEX IF NOT EXISTS idx_auditlog_user_action     ON auditlog(user_id, action)",
            "CREATE INDEX IF NOT EXISTS idx_auditlog_created_at      ON auditlog(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_apikey_hash              ON apikey(hashed_key)",
            # Notifications — feed in-app por usuario. La tabla la crea
            # SQLModel.metadata.create_all (modelo Notification); aquí solo
            # los índices compuestos para queries típicas (bell + filtro).
            "CREATE INDEX IF NOT EXISTS idx_notif_user_unread        ON notification(user_id, is_read, created_at)",
            "CREATE INDEX IF NOT EXISTS idx_notif_tenant_user        ON notification(tenant_id, user_id)",
            "CREATE INDEX IF NOT EXISTS idx_notif_entity             ON notification(entity_type, entity_id)",
            # ================================================================
            # Multi-tenancy — añadir tenant_id a todas las tablas raíz
            # ================================================================
            # NOTA: la tabla `tenant` la crea SQLModel.metadata.create_all.
            # Aquí sólo añadimos columnas FK + backfill. Todas son IF NOT EXISTS.
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS is_superadmin BOOLEAN NOT NULL DEFAULT FALSE',
            # Perfil + tracking (vista Control de Accesos).
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS phone VARCHAR(64)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS department VARCHAR(128)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS position VARCHAR(128)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS created_at VARCHAR(64)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS last_login_at VARCHAR(64)',
            # Mi Perfil — campos extendidos + preferencias de notificación.
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS location VARCHAR(160)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS bio TEXT',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS avatar_url VARCHAR(512)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS updated_at VARCHAR(64)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS notif_email_enabled BOOLEAN NOT NULL DEFAULT TRUE',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS notif_push_enabled BOOLEAN NOT NULL DEFAULT TRUE',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS notif_meeting_reminders BOOLEAN NOT NULL DEFAULT TRUE',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS notif_task_assigned BOOLEAN NOT NULL DEFAULT TRUE',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS notif_session_processed BOOLEAN NOT NULL DEFAULT TRUE',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS notif_weekly_report BOOLEAN NOT NULL DEFAULT FALSE',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS notif_security_alerts BOOLEAN NOT NULL DEFAULT TRUE',
            # 2FA email-OTP
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS two_factor_enabled BOOLEAN NOT NULL DEFAULT FALSE',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS two_factor_method VARCHAR(32) DEFAULT \'email\'',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS two_factor_code_hash VARCHAR(255)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS two_factor_code_expires_at VARCHAR(64)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS two_factor_code_purpose VARCHAR(32)',
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS two_factor_attempts INTEGER NOT NULL DEFAULT 0',
            # Soft delete — el admin "elimina" usuarios desde /admin/users.
            # Conservamos la fila (para no romper FKs en ActionItem.owner_email,
            # AuditLog.user_id, etc.) pero la excluimos de listas y logins.
            'ALTER TABLE "user"             ADD COLUMN IF NOT EXISTS deleted_at VARCHAR(64)',
            # Role — columnas nuevas que SQLModel mapea pero la DB heredada no tiene.
            "ALTER TABLE role               ADD COLUMN IF NOT EXISTS is_system  BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE role               ADD COLUMN IF NOT EXISTS created_at VARCHAR(64)",
            "ALTER TABLE role               ADD COLUMN IF NOT EXISTS updated_at VARCHAR(64)",
            'ALTER TABLE project            ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            'ALTER TABLE meetingsession     ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            'ALTER TABLE integrationsetting ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            'ALTER TABLE outputtemplate     ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            'ALTER TABLE actionitem         ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            'ALTER TABLE apikey             ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            'ALTER TABLE auditlog           ADD COLUMN IF NOT EXISTS tenant_id INTEGER',
            # Índices para queries multi-tenant
            "CREATE INDEX IF NOT EXISTS idx_user_tenant            ON \"user\"(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_project_tenant         ON project(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_meetingsession_tenant  ON meetingsession(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_integration_tenant     ON integrationsetting(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_outputtemplate_tenant  ON outputtemplate(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_actionitem_tenant      ON actionitem(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_apikey_tenant          ON apikey(tenant_id)",
            "CREATE INDEX IF NOT EXISTS idx_auditlog_tenant        ON auditlog(tenant_id)",
            # Landing CMS — contenido editable de acten.app (solo tenant 'acten').
            "ALTER TABLE tenant ADD COLUMN IF NOT EXISTS landing_content_json TEXT NOT NULL DEFAULT '{}'",
            # ============================================================
            # Integraciones per-user (Trello/Jira/ClickUp/Azure + Calendar).
            # ============================================================
            # Antes IntegrationSetting era per-tenant pura. Ahora coexisten:
            #   - filas con user_id IS NULL → per-tenant (resend, fireflies, branding…)
            #   - filas con user_id IS NOT NULL → per-user (trello, jira…)
            # El cambio del UNIQUE se hace en _ensure_default_tenant_and_backfill
            # porque depende de DROP + CREATE INDEX (no ALTER ADD COLUMN).
            'ALTER TABLE integrationsetting ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES "user"(id)',
            "CREATE INDEX IF NOT EXISTS idx_integrationsetting_user ON integrationsetting(user_id)",
            # Routing per-user dentro del proyecto.
            'ALTER TABLE routing ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES "user"(id)',
            "CREATE INDEX IF NOT EXISTS idx_routing_user ON routing(user_id)",
            # Owner del tenant + switches share_* (modelo compartido vs per-user).
            # Defaults: share_integrations=TRUE (el owner pone tokens una vez),
            # share_routings=FALSE (cada uno decide dónde aterrizan SUS tareas).
            'ALTER TABLE tenant ADD COLUMN IF NOT EXISTS owner_user_id INTEGER REFERENCES "user"(id)',
            "CREATE INDEX IF NOT EXISTS idx_tenant_owner ON tenant(owner_user_id)",
            "ALTER TABLE tenant ADD COLUMN IF NOT EXISTS share_integrations BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE tenant ADD COLUMN IF NOT EXISTS share_routings BOOLEAN NOT NULL DEFAULT FALSE",
        ]

    from sqlalchemy import text

    with engine.begin() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "duplicate column" in msg or "already exists" in msg:
                    continue
                logger.warning("Migración ignorada (%s): %s", stmt, exc)

    # Backfill multi-tenant + cambio de uniques (solo Postgres real)
    if not _is_sqlite:
        _ensure_default_tenant_and_backfill()


def _ensure_default_tenant_and_backfill() -> None:
    """Crea el tenant 'acten' por defecto y asigna a él todos los registros
    legacy que aún no tengan `tenant_id`. También promociona la antigua
    `IntegrationSetting('branding')` (singleton) a `Tenant.branding_json`
    y reemplaza los uniques globales por los compuestos `(col, tenant_id)`.

    Idempotente: cada paso comprueba antes de actuar.
    """
    from sqlalchemy import text

    with engine.begin() as conn:
        # 1) Asegurar el tenant default. La tabla la creó create_all.
        row = conn.execute(
            text("SELECT id FROM tenant WHERE slug = :s"),
            {"s": DEFAULT_TENANT_SLUG},
        ).first()
        if row:
            tenant_id = row[0]
        else:
            res = conn.execute(
                text(
                    "INSERT INTO tenant (slug, name, branding_json, is_active, created_at) "
                    "VALUES (:s, :n, '{}', TRUE, NOW()::text) RETURNING id"
                ),
                {"s": DEFAULT_TENANT_SLUG, "n": DEFAULT_TENANT_NAME},
            )
            tenant_id = res.scalar_one()
            logger.info("Tenant default creado: %s (id=%s)", DEFAULT_TENANT_SLUG, tenant_id)

        # 2) Backfill: cualquier fila con tenant_id IS NULL → tenant default.
        #    Listo en orden topológico para no romper FK durante el ALTER NOT NULL.
        backfill_tables = [
            '"user"', "project", "meetingsession", "integrationsetting",
            "outputtemplate", "actionitem", "apikey", "auditlog",
        ]
        for tbl in backfill_tables:
            conn.execute(
                text(f"UPDATE {tbl} SET tenant_id = :t WHERE tenant_id IS NULL"),
                {"t": tenant_id},
            )

        # 3) Promover el primer admin (`admin@notiva.local` o el más antiguo)
        #    a super-admin si nadie lo es aún. Permite gestionar otros tenants.
        has_super = conn.execute(text('SELECT 1 FROM "user" WHERE is_superadmin = TRUE LIMIT 1')).first()
        if not has_super:
            conn.execute(text(
                'UPDATE "user" SET is_superadmin = TRUE '
                "WHERE id = (SELECT id FROM \"user\" ORDER BY id ASC LIMIT 1)"
            ))
            logger.info("Promovido el primer usuario a super-admin (gestión de tenants).")

        # 4) Migrar branding del singleton legacy a Tenant.branding_json.
        legacy_brand = conn.execute(text(
            "SELECT config_json FROM integrationsetting "
            "WHERE provider_name = 'branding' AND tenant_id = :t LIMIT 1"
        ), {"t": tenant_id}).first()
        if legacy_brand and legacy_brand[0] and legacy_brand[0] not in ("", "{}"):
            existing = conn.execute(
                text("SELECT branding_json FROM tenant WHERE id = :t"),
                {"t": tenant_id},
            ).first()
            if not existing or existing[0] in ("", "{}"):
                conn.execute(
                    text("UPDATE tenant SET branding_json = :b WHERE id = :t"),
                    {"b": legacy_brand[0], "t": tenant_id},
                )
                logger.info("Branding legacy migrado a Tenant.branding_json.")

        # 5) Reemplazar UNIQUE globales por UNIQUE compuestos (col, tenant_id).
        # SQLAlchemy genera el nombre como `ix_<table>_<col>` cuando el Field
        # tiene `index=True, unique=True`, y `<table>_<col>_key` cuando es
        # `unique=True` sin index. Probamos los dos por seguridad.
        replace_uniques = [
            ("user", ["user_email_key", "ix_user_email"],
             "uq_user_email_per_tenant", "(email, tenant_id)"),
            ("project", ["project_name_key", "ix_project_name"],
             "uq_project_name_per_tenant", "(name, tenant_id)"),
            ("meetingsession", ["ix_meetingsession_fireflies_id"],
             "uq_meetingsession_ff_per_tenant", "(fireflies_id, tenant_id)"),
            ("integrationsetting",
             ["integrationsetting_provider_name_key", "ix_integrationsetting_provider_name"],
             "uq_integration_per_tenant", "(provider_name, tenant_id)"),
            ("outputtemplate",
             ["outputtemplate_name_key", "ix_outputtemplate_name"],
             "uq_outputtemplate_name_per_tenant", "(name, tenant_id)"),
        ]
        for table, old_idx_names, new_idx, cols in replace_uniques:
            for old_idx in old_idx_names:
                conn.execute(text(f'ALTER TABLE "{table}" DROP CONSTRAINT IF EXISTS "{old_idx}"'))
                conn.execute(text(f'DROP INDEX IF EXISTS "{old_idx}"'))
            # Re-crear el `ix_*` (NO unique) sólo para los que necesitan índice
            # de búsqueda por la columna sola. user.email y project.name lo
            # necesitan para WHERE email = X AND tenant_id = Y.
            if table in ("user", "project", "outputtemplate", "integrationsetting"):
                col_name = cols.strip("()").split(",")[0].strip()
                conn.execute(text(
                    f'CREATE INDEX IF NOT EXISTS "ix_{table}_{col_name}_nonunique" '
                    f'ON "{table}" ({col_name})'
                ))
            # Crear el unique compuesto.
            exists = conn.execute(text(
                "SELECT 1 FROM pg_indexes WHERE indexname = :n"
            ), {"n": new_idx}).first()
            if not exists:
                conn.execute(text(
                    f'CREATE UNIQUE INDEX "{new_idx}" ON "{table}" {cols}'
                ))

        # 6) Una vez backfilleado, podemos exigir NOT NULL en tenant_id.
        for tbl, col in [
            ('"user"', "tenant_id"),
            ("project", "tenant_id"),
            ("meetingsession", "tenant_id"),
            ("integrationsetting", "tenant_id"),
            ("outputtemplate", "tenant_id"),
            ("actionitem", "tenant_id"),
            ("apikey", "tenant_id"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE {tbl} ALTER COLUMN {col} SET NOT NULL"))
            except Exception as exc:
                # Si quedan NULLs por una tabla derivada que no backfilleamos
                # (caso raro), no abortamos el boot — solo lo registramos.
                logger.warning("No pude SET NOT NULL %s.%s: %s", tbl, col, exc)

        # 7) Migración a integraciones per-user.
        # --------------------------------------------------------------
        # IntegrationSetting: reemplazar el UNIQUE (provider_name, tenant_id)
        # por DOS índices parciales — uno para per-tenant (user_id NULL) y
        # otro para per-user. SQLAlchemy unique constraint no acepta WHERE,
        # así que lo hacemos en SQL crudo.
        #
        # Idempotente: comprobamos existencia antes de crear.
        # --------------------------------------------------------------
        try:
            conn.execute(text(
                "ALTER TABLE integrationsetting DROP CONSTRAINT IF EXISTS uq_integration_per_tenant"
            ))
            conn.execute(text("DROP INDEX IF EXISTS uq_integration_per_tenant"))
        except Exception as exc:
            logger.warning("No pude dropear unique legacy de integrationsetting: %s", exc)

        # Per-tenant: una sola fila por (provider, tenant) cuando user_id IS NULL.
        partial_per_tenant = conn.execute(text(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_integration_tenant_global'"
        )).first()
        if not partial_per_tenant:
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_integration_tenant_global "
                    "ON integrationsetting (provider_name, tenant_id) "
                    "WHERE user_id IS NULL"
                ))
            except Exception as exc:
                logger.warning("No pude crear uq_integration_tenant_global: %s", exc)

        # Per-user: una sola fila por (provider, tenant, user) cuando user_id NOT NULL.
        partial_per_user = conn.execute(text(
            "SELECT 1 FROM pg_indexes WHERE indexname = 'uq_integration_per_user'"
        )).first()
        if not partial_per_user:
            try:
                conn.execute(text(
                    "CREATE UNIQUE INDEX uq_integration_per_user "
                    "ON integrationsetting (provider_name, tenant_id, user_id) "
                    "WHERE user_id IS NOT NULL"
                ))
            except Exception as exc:
                logger.warning("No pude crear uq_integration_per_user: %s", exc)

        # Backfill: los IntegrationSetting legacy de proveedores per-user
        # (trello/jira/clickup/azure) probablemente los puso un admin —
        # asignamos esa config al admin más antiguo (no superadmin global)
        # del mismo tenant. Si no hay admin no superadmin, usamos el primer
        # user del tenant. Evita perder credenciales en la migración.
        conn.execute(text(
            "UPDATE integrationsetting AS i "
            "SET user_id = sub.uid "
            "FROM ( "
            "  SELECT i2.id AS iid, ( "
            "    SELECT u.id FROM \"user\" u "
            "    WHERE u.tenant_id = i2.tenant_id "
            "      AND u.deleted_at IS NULL "
            "    ORDER BY (u.is_superadmin) ASC, u.id ASC "
            "    LIMIT 1 "
            "  ) AS uid "
            "  FROM integrationsetting i2 "
            "  WHERE i2.user_id IS NULL "
            "    AND i2.provider_name IN ('trello','jira','clickup','azure') "
            ") AS sub "
            "WHERE i.id = sub.iid AND sub.uid IS NOT NULL"
        ))

        # Routing: backfill al primer user del tenant del proyecto. Mismo
        # criterio que IntegrationSetting per-user.
        conn.execute(text(
            "UPDATE routing AS r "
            "SET user_id = sub.uid "
            "FROM ( "
            "  SELECT r2.id AS rid, ( "
            "    SELECT u.id FROM \"user\" u "
            "    JOIN project p ON p.tenant_id = u.tenant_id "
            "    WHERE p.id = r2.project_id "
            "      AND u.deleted_at IS NULL "
            "    ORDER BY (u.is_superadmin) ASC, u.id ASC "
            "    LIMIT 1 "
            "  ) AS uid "
            "  FROM routing r2 "
            "  WHERE r2.user_id IS NULL "
            ") AS sub "
            "WHERE r.id = sub.rid AND sub.uid IS NOT NULL"
        ))

        # 8) Backfill tenant.owner_user_id — el usuario más antiguo del
        # tenant. NO discrimina superadmin (a diferencia del backfill de
        # IntegrationSetting/Routing) — porque en producción Acten el
        # primer user ES superadmin (fcortes) y debe seguir siendo el owner.
        # Solo backfilleamos cuando el campo está NULL — un futuro UI para
        # transferir ownership puede sobrescribirlo manualmente.
        conn.execute(text(
            "UPDATE tenant AS t "
            "SET owner_user_id = sub.uid "
            "FROM ( "
            "  SELECT t2.id AS tid, ( "
            "    SELECT u.id FROM \"user\" u "
            "    WHERE u.tenant_id = t2.id "
            "      AND u.deleted_at IS NULL "
            "    ORDER BY u.id ASC "
            "    LIMIT 1 "
            "  ) AS uid "
            "  FROM tenant t2 "
            "  WHERE t2.owner_user_id IS NULL "
            ") AS sub "
            "WHERE t.id = sub.tid AND sub.uid IS NOT NULL"
        ))


def get_session():
    """Dependencia FastAPI: yields una sesión por request."""
    with Session(engine) as session:
        yield session
