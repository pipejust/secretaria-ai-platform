from sqlmodel import create_engine, SQLModel, Session
from config import settings

# Engine global 
engine = create_engine(
    settings.database_url,
    echo=True, # Puede deshabilitarse en prod
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {}
)

def create_db_and_tables():
    """Crea la base de datos y todas las tablas."""
    SQLModel.metadata.create_all(engine)

def get_session():
    """Generador para obtener la sesión de la DB (útil para FastAPI Depends)."""
    with Session(engine) as session:
        yield session
