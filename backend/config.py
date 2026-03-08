from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    project_name: str = "Secretaría AI"
    database_url: str = "sqlite:///./secretaria.db" # Default to SQLite for easy local dev, can be Supabase PostgresURL
    groq_api_key: str = ""
    fireflies_api_key: str = ""
    
    # Configuraciones de Integraciones (Trello, Azure DevOps, etc.)
    # Estas pueden ir en DB según el proyecto, pero algunas globales podrían estar aquí.
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
