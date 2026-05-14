from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    project_name: str = "Notiva"
    database_url: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""
    fireflies_api_key: str = ""
    supabase_url: str = ""
    supabase_key: str = ""
    frontend_url: str = "http://localhost:4200"

    # Authentication
    jwt_secret_key: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24
    bcrypt_rounds: int = 12

    # Gotenberg (DOCX → PDF). En docker compose se setea a http://gotenberg:3000
    # En producción debería apuntar al contenedor local. Si está vacío usamos
    # el demo público como último fallback (NO recomendado en prod).
    gotenberg_url: str = "http://gotenberg:3000"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
