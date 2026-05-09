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
        # SQLite acepta ADD COLUMN pero no IF NOT EXISTS. Lo intentamos y si falla por
        # 'duplicate column name' lo ignoramos.
        statements = [
            'ALTER TABLE meetingsession ADD COLUMN ai_fields_regenerated BOOLEAN DEFAULT 0 NOT NULL',
            'ALTER TABLE meetingsession ADD COLUMN ai_tasks_regenerated  BOOLEAN DEFAULT 0 NOT NULL',
            'ALTER TABLE project ADD COLUMN auto_dispatch_enabled BOOLEAN',
            'ALTER TABLE project ADD COLUMN auto_dispatch_timeout_hours REAL',
        ]
    else:
        statements = [
            'ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS ai_fields_regenerated BOOLEAN DEFAULT FALSE NOT NULL',
            'ALTER TABLE meetingsession ADD COLUMN IF NOT EXISTS ai_tasks_regenerated  BOOLEAN DEFAULT FALSE NOT NULL',
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS auto_dispatch_enabled BOOLEAN',
            'ALTER TABLE project ADD COLUMN IF NOT EXISTS auto_dispatch_timeout_hours DOUBLE PRECISION',
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
