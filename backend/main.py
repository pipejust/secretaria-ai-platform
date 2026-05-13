import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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

# Configurar CORS para permitir peticiones desde el frontend Angular ANTES de cargar los routers.
# Origins:
#   - Desarrollo local (Angular dev server)
#   - FRONTEND_URL inyectada por env (prod: https://acten.app)
#   - Variantes www y apex de FRONTEND_URL (sin tener que duplicar config)
#   - Legacy de Vercel/Render (compat hasta retirar dominios viejos)
_frontend_url = os.environ.get("FRONTEND_URL", "").rstrip("/")
_dynamic_origins: list[str] = []
if _frontend_url:
    _dynamic_origins.append(_frontend_url)
    # Añade contraparte www/apex (acten.app ↔ www.acten.app)
    if "://www." in _frontend_url:
        _dynamic_origins.append(_frontend_url.replace("://www.", "://"))
    else:
        _dynamic_origins.append(_frontend_url.replace("://", "://www."))

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        # Dev local
        "http://localhost:52481",
        "http://127.0.0.1:52481",
        "http://localhost:4200",
        "http://127.0.0.1:4200",
        # Prod (acten.app + www) inyectado por env
        *_dynamic_origins,
        # Legacy (Vercel/Render) — TODO: retirar tras corte definitivo
        "https://secretaria-api.vercel.app",
        "https://frontend-alpha-gules-63.vercel.app",
        "https://secretaria-ai-platform.vercel.app",
        "https://secretaria-ai-platform.onrender.com",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static mount para servir avatares subidos por usuarios (Mi Perfil).
_UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(os.path.join(_UPLOADS_DIR, "avatars"), exist_ok=True)
app.mount("/static", StaticFiles(directory=_UPLOADS_DIR), name="static-uploads")

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

    # Migración ligera: marcar roles canónicos como `is_system=True`,
    # rellenar timestamps y SIEMPRE garantizar que admin tenga TODOS los
    # permisos del catálogo. Idempotente.
    try:
        from models import Role, RolePermission, RoleActivity
        from routers.auth import ROLE_CATALOG, VALID_MODULES
        from datetime import datetime as _dt
        with Session(engine) as session:
            roles = session.exec(select(Role)).all()
            now_iso = _dt.now().isoformat()
            changed = False
            for r in roles:
                if r.name.lower() in {"admin", "validator", "viewer"} and not r.is_system:
                    r.is_system = True
                    session.add(r); changed = True
                if not getattr(r, "created_at", None):
                    r.created_at = now_iso; session.add(r); changed = True
                if not getattr(r, "updated_at", None):
                    r.updated_at = now_iso; session.add(r); changed = True
            if changed:
                session.commit()
                logger.info("Migración de roles: campos system/timestamps normalizados.")

            # Garantizar permisos para el rol admin: todo (module × action).
            admin_role = session.exec(select(Role).where(Role.name == "admin")).first()
            if admin_role and admin_role.id:
                existing = session.exec(
                    select(RolePermission).where(RolePermission.role_id == admin_role.id)
                ).all()
                existing_pairs = {(p.module_key, p.action) for p in existing}
                added = 0
                for module_key, actions in VALID_MODULES.items():
                    for action in actions:
                        if (module_key, action) not in existing_pairs:
                            session.add(RolePermission(
                                role_id=admin_role.id,
                                module_key=module_key,
                                action=action,
                                is_granted=True,
                            ))
                            added += 1
                if added:
                    session.commit()
                    logger.info("Seed admin: %d permisos agregados al rol admin.", added)

            # Sembrar entrada inicial de actividad para roles sin historial,
            # así "Actividad reciente" no se ve vacía la primera vez.
            for r in roles:
                if not r.id:
                    continue
                already = session.exec(
                    select(RoleActivity).where(RoleActivity.role_id == r.id)
                ).first()
                if already:
                    continue
                session.add(RoleActivity(
                    role_id=r.id,
                    action="created",
                    actor_user_id=None,
                    actor_name="Sistema",
                    note=f"Rol '{r.name}' inicializado por el sistema.",
                    created_at=getattr(r, "created_at", None) or now_iso,
                ))
            session.commit()
    except Exception:
        logger.exception("Seed de roles/permisos falló (no bloquea startup).")

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
from routers import users_directory  # Directorio: resolve email → User del tenant
app.include_router(users_directory.router)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Acten Backend está corriendo"}
