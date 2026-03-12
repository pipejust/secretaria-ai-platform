from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from pydantic import BaseModel
from database import create_db_and_tables
from routers import fireflies
from routers import auth
from routers import users
from routers import templates
from routers import projects

app = FastAPI(
    title="Secretaría AI Backend",
    description="Orquestador principal para procesamiento de actas y tareas",
    version="1.0.0"
)

# Configurar CORS para permitir peticiones desde el frontend Angular ANTES de cargar los routers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:52481",
        "http://127.0.0.1:52481",
        "https://secretaria-api.vercel.app",
        "https://frontend-alpha-gules-63.vercel.app",
        "https://secretaria-ai-platform.vercel.app",
        "https://secretaria-ai-platform.onrender.com"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

app.include_router(fireflies.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(templates.router)
from routers import settings
app.include_router(settings.router)
from routers import debug
app.include_router(debug.router)
app.include_router(projects.router)

from routers import documental
app.include_router(documental.router)
from routers import landing
app.include_router(landing.router)
from routers import rondas
app.include_router(rondas.router)
from routers import sessions_upload
app.include_router(sessions_upload.router)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Secretaría AI Backend está corriendo"}
