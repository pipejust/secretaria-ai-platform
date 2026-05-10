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


def _apply_lightweight_migrations() -> None:
    """ALTER TABLE idempotentes para columnas añadidas después del schema inicial."""
    if _is_sqlite:
        statements = [
            'ALTER TABLE meetingsession ADD COLUMN ai_fields_regenerated BOOLEAN DEFAULT 0 NOT NULL',
            'ALTER TABLE meetingsession ADD COLUMN ai_tasks_regenerated  BOOLEAN DEFAULT 0 NOT NULL',
            'ALTER TABLE project ADD COLUMN auto_dispatch_enabled BOOLEAN',
            'ALTER TABLE project ADD COLUMN auto_dispatch_timeout_hours REAL',
            'ALTER TABLE project ADD COLUMN owner_user_id INTEGER REFERENCES "user"(id)',
            'ALTER TABLE actionitem ADD COLUMN status TEXT DEFAULT "pending" NOT NULL',
            'ALTER TABLE actionitem ADD COLUMN completed_at TEXT',
            # Sprint 00 — embeddings (sqlite no soporta pgvector, fallback a TEXT)
            'ALTER TABLE embeddingchunk ADD COLUMN embedding_vector TEXT',
        ]
    else:
        statements = [
            'ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS ai_fields_regenerated BOOLEAN DEFAULT FALSE NOT NULL',
            'ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS ai_tasks_regenerated  BOOLEAN DEFAULT FALSE NOT NULL',
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS auto_dispatch_enabled BOOLEAN',
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS auto_dispatch_timeout_hours DOUBLE PRECISION',
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS owner_user_id INTEGER REFERENCES "user"(id)',
            'ALTER TABLE actionitem ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT \'pending\'',
            'ALTER TABLE actionitem ADD COLUMN IF NOT EXISTS completed_at VARCHAR(64)',
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


def get_session():
    """Dependencia FastAPI: yields una sesión por request."""
    with Session(engine) as session:
        yield session
