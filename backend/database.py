from sqlmodel import create_engine, SQLModel, Session
from config import settings

# Agregamos pool_pre_ping para evitar conexiones muertas y configuramos el connection pool para Supabase
engine = create_engine(
    settings.database_url,
    echo=False, # logs de DB apagados para mejor performance
    pool_size=10, 
    max_overflow=20,
    pool_pre_ping=True
)

def create_db_and_tables():
    """Crea la base de datos y todas las tablas."""
    SQLModel.metadata.create_all(engine)

def get_session():
    """Generador para obtener la sesión de la DB (útil para FastAPI Depends)."""
    with Session(engine) as session:
        yield session
