from pydantic_settings import BaseSettings, SettingsConfigDict

import os

class Settings(BaseSettings):
    project_name: str = "Secretaría AI"
    # By default, use sqlite in local dir. If running on Vercel (read-only FS), use /tmp
    database_url: str = "sqlite:////tmp/secretaria.db" if os.environ.get("VERCEL") else "sqlite:///./secretaria.db"
    groq_api_key: str = ""
    fireflies_api_key: str = ""
    
    # Configuraciones de Integraciones (Trello, Azure DevOps, etc.)
    # Estas pueden ir en DB según el proyecto, pero algunas globales podrían estar aquí.
    
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
