from pydantic_settings import BaseSettings, SettingsConfigDict

import os

class Settings(BaseSettings):
    project_name: str = "Secretaría AI"
    # Render provides persistent disks, so we can use a relative file or an absolute path on their disk
    database_url: str = os.environ.get("DATABASE_URL", "sqlite:///./secretaria.db")
    groq_api_key: str = ""
    fireflies_api_key: str = ""
    
    # Configuraciones de Integraciones (Trello, Azure DevOps, etc.)
    # Estas pueden ir en DB según el proyecto, pero algunas globales podrían estar aquí.
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
