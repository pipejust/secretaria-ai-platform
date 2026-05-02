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
    """Crea la base de datos y todas las tablas si no existen."""
    SQLModel.metadata.create_all(engine)


def get_session():
    """Dependencia FastAPI: yields una sesión por request."""
    with Session(engine) as session:
        yield session
