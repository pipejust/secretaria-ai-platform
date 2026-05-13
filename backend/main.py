import logging
import os

from fastapi import FastAPI, Request
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
    title="Acten Backend",
    description="Acten — plataforma white-label de actas y tareas. Procesa reuniones, extrae decisiones/tareas y dispara correos/integraciones con la marca de cada cliente.",
    version="1.0.0"
)

# === Sprint 11 — Rate limiting global con slowapi ===
# Solo se activa si slowapi está instalado. En entornos sin la dep, sigue.
try:
    from slowapi import Limiter
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    from slowapi.util import get_remote_address

    limiter = Limiter(key_func=get_remote_address, default_limits=["120/minute"])
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rl_exceeded(request: Request, exc: RateLimitExceeded):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": f"Rate limit exceeded: {exc.detail}"}, status_code=429)

    app.add_middleware(SlowAPIMiddleware)
    logger.info("Rate limiter activo (120 req/min por IP).")
except ImportError:
    logger.warning("slowapi no instalado; rate limiting desactivado.")

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

    # Seed automático en development (admin@notiva.local / notiva)
    if os.getenv("ENVIRONMENT", "").lower() in ("dev", "development"):
        try:
            import sys, pathlib
            sys.path.insert(0, str(pathlib.Path(__file__).parent / "scripts"))
            from seed_dev import main as seed_main
            seed_main()
        except Exception:
            logger.exception("seed_dev falló (no bloquea startup).")

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
from routers import pendientes
app.include_router(pendientes.router)
from routers import reports
app.include_router(reports.router)
from routers import ask
app.include_router(ask.router)
from routers import calendar as calendar_router  # Sprint 03
app.include_router(calendar_router.router)
from routers import role_outputs  # Sprint 04
app.include_router(role_outputs.router)
from routers import collab  # Sprint 07
app.include_router(collab.router)
from routers import me  # Sprint 08 — GDPR
app.include_router(me.router)
from routers import analytics  # Sprint 10
app.include_router(analytics.router)
from routers import api_keys  # Sprint 11
app.include_router(api_keys.router)
from routers import branding  # White-label
app.include_router(branding.router)
from routers import tenants  # Multi-tenant CRUD (super-admin)
app.include_router(tenants.router)
app.include_router(tenants.public_router)
from routers import notifications  # Notifications in-app (bell del topbar)
app.include_router(notifications.router)
from routers import search  # Búsqueda global (search del topbar)
app.include_router(search.router)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Acten Backend está corriendo"}
