"""Configuración común de pytest para Notiva.

- Engine SQLite en memoria por test (rápido + aislado).
- Sobrescribe `database.engine` y `get_session` para que la app use el de test.
- Settings con valores seguros para evitar llamar a OpenAI/Groq/Resend reales.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Forzar settings de test ANTES de importar nada del proyecto
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)
os.environ.setdefault("BCRYPT_ROUNDS", "4")
os.environ.setdefault("OPENAI_API_KEY", "")    # vacío = stub en embeddings/llm
os.environ.setdefault("GROQ_API_KEY", "")
os.environ.setdefault("FIREFLIES_API_KEY", "")

# Permitir imports estilo `from models import X` cuando pytest corre desde repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool


@pytest.fixture(scope="session")
def test_engine():
    """Engine SQLite en memoria compartido entre conexiones."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    yield engine


@pytest.fixture()
def db_session(test_engine):
    """Sesión SQL fresca por test, con rollback al final."""
    with Session(test_engine) as s:
        yield s
        s.rollback()


@pytest.fixture()
def client(test_engine, monkeypatch):
    """TestClient con engine de test inyectado."""
    import database
    monkeypatch.setattr(database, "engine", test_engine)

    def _get_session_override():
        with Session(test_engine) as s:
            yield s

    monkeypatch.setattr(database, "get_session", _get_session_override)

    # Importar main DESPUÉS del monkeypatch
    from main import app
    return TestClient(app)
