import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database import create_db_and_tables
from routers import auth, fireflies, projects, templates, users
from services.cron_service import start_cron, stop_cron

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Notiva Backend",
    description="Orquestador principal para procesamiento de actas y tareas",
    version="1.0.0"
)

# Configurar CORS para permitir peticiones desde el frontend Angular ANTES de cargar los routers
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:52481",
        "http://127.0.0.1:52481",
        "http://localhost:4200",
        "http://127.0.0.1:4200",
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
    
    # Restablecer reuniones atascadas de corridas anteriores
    from sqlmodel import Session, select
    from database import engine
    from models import MeetingSession
    with Session(engine) as session:
        stuck_sessions = session.exec(select(MeetingSession).where(MeetingSession.status == "processing")).all()
        for s in stuck_sessions:
            s.status = "pending"
            session.add(s)
        if stuck_sessions:
            session.commit()
            logger.info(
                "Restauradas %d sesiones atascadas (processing -> pending).",
                len(stuck_sessions),
            )

    start_cron()

@app.on_event("shutdown")
def on_shutdown():
    stop_cron()

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
    return {"status": "ok", "message": "Notiva Backend está corriendo"}
