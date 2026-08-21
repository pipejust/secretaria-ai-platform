import logging
import os

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
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
#   - Variantes www, apex y admin de FRONTEND_URL (sin duplicar config)
#   - Legacy de Vercel/Render (compat hasta retirar dominios viejos)
#
# Reglas de derivación:
#   acten.app           → +www.acten.app +admin.acten.app
#   www.acten.app       → +acten.app     +admin.acten.app
#   admin.acten.app     → +acten.app     +www.acten.app
_frontend_url = os.environ.get("FRONTEND_URL", "").rstrip("/")
_dynamic_origins: list[str] = []
if _frontend_url:
    _dynamic_origins.append(_frontend_url)
    # Calculamos el apex (sin www. ni admin. al frente) para derivar todas
    # las variantes. Esto evita que cualquier subdominio nuevo tenga que
    # agregarse a mano otra vez.
    if "://www." in _frontend_url:
        _apex = _frontend_url.replace("://www.", "://")
    elif "://admin." in _frontend_url:
        _apex = _frontend_url.replace("://admin.", "://")
    else:
        _apex = _frontend_url
    # Sumamos las tres variantes canónicas. El set de duplicados se quita
    # más abajo al concatenar con dev/legacy.
    _dynamic_origins.append(_apex)
    _dynamic_origins.append(_apex.replace("://", "://www."))
    _dynamic_origins.append(_apex.replace("://", "://admin."))
    # Deduplica preservando orden
    _dynamic_origins = list(dict.fromkeys(_dynamic_origins))

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
    # max_age default es 600 (10 min). Si una preflight falla durante una
    # transición de deploy, el browser cachea el rechazo CORS por 10 min y
    # el usuario ve "blocked by CORS policy" aunque el server ya responda
    # bien. Bajamos a 60 s — suficiente para ahorrar round-trips, lo bastante
    # corto para que un deploy roto no inutilice la app por 10 min.
    max_age=60,
)

# Static mount para servir avatares subidos por usuarios (Mi Perfil).
_UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(os.path.join(_UPLOADS_DIR, "avatars"), exist_ok=True)
app.mount("/static", StaticFiles(directory=_UPLOADS_DIR), name="static-uploads")


# ────────────────────────────────────────────────────────────────────────────
# SEO middleware — el API en api.acten.app NO debe ser indexado por Google.
# Sin esto, los crawlers podrían indexar los OpenAPI/docs y exponer la
# superficie del API públicamente en SERPs.
# ────────────────────────────────────────────────────────────────────────────
@app.middleware("http")
async def add_seo_headers(request: Request, call_next):
    response = await call_next(request)
    # Bloqueamos indexación del subdominio API completo.
    response.headers.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive, nosnippet")
    return response


# ────────────────────────────────────────────────────────────────────────────
# robots.txt restrictivo en el subdominio API.
# Si un crawler igual intenta indexar api.acten.app, encuentra Disallow: /
# ────────────────────────────────────────────────────────────────────────────
_API_ROBOTS_TXT = """# Acten API — subdominio NO indexable
# Toda la superficie del API queda fuera de los buscadores.
# Para SEO de la app, ver https://acten.app/robots.txt

User-agent: *
Disallow: /
"""


@app.get("/robots.txt", include_in_schema=False)
def api_robots_txt() -> PlainTextResponse:
    return PlainTextResponse(content=_API_ROBOTS_TXT, media_type="text/plain")


# ────────────────────────────────────────────────────────────────────────────
# favicon.ico para que cuando un browser/crawler haga GET api.acten.app/favicon.ico
# devolvamos el mismo favicon de la marca en vez de 404.
# El archivo se busca en backend/uploads/favicon.ico (lo monta Coolify si existe);
# si no, fallback a un 204 No Content para no spamear logs con 404.
# ────────────────────────────────────────────────────────────────────────────
_FAVICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.ico")


@app.get("/favicon.ico", include_in_schema=False)
def api_favicon() -> Response:
    if os.path.isfile(_FAVICON_PATH):
        return FileResponse(
            _FAVICON_PATH,
            media_type="image/x-icon",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return Response(status_code=204)


@app.on_event("startup")
def on_startup():
    create_db_and_tables()

    # Convierte a cifrado lo que quedó guardado en claro antes de que el
    # cifrado existiera. Idempotente y barato: en una base ya convertida
    # no escribe nada.
    try:
        from services.cifrado import cifrar_secretos_pendientes
        cifrar_secretos_pendientes()
    except Exception:  # noqa: BLE001
        logger.exception("El cifrado de credenciales pendientes falló (no bloquea).")
    
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
app.include_router(landing.router)         # /api/landing-cms (admin)
app.include_router(landing.public_router)  # /api/public/landing (público)
app.include_router(landing.legacy_router)  # /api/v1/landing_page_content (legacy)
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
# El router del Sprint 03 (`/api/calendar`) queda desmontado: lo reemplaza
# entero el módulo de abajo. Pedía permisos de solo lectura, no sabía de
# calendarios ni de permisos, y solo admitía una cuenta por proveedor y
# persona. El archivo se conserva como referencia de lo que había; nada
# lo importa ya.

# El módulo de calendarios: la lista con permisos, los eventos propios y
# los traídos de Google/Microsoft/Zoho, y la suscripción por .ics. El
# router de arriba (`/api/calendar`) es el anterior y solo queda vivo
# para lo que aún lo consume; lo nuevo cuelga de /api/v1/calendars.
from routers import calendars as calendars_v2  # noqa: E402
app.include_router(calendars_v2.router)

# La llave del motor de IA, editable por el administrador de cada empresa.
from routers import ai_keys  # noqa: E402
app.include_router(ai_keys.router)
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
# API pública v1 — integración con la plataforma de Servicios/RRHH.
# Auth por X-API-Key + scopes. Ver docs/INTEGRACION_ACTEN_RRHH.md
from routers import integration_v1
app.include_router(integration_v1.router)
# Asistente de enlace bidireccional (wizard de onboarding entre plataformas)
from routers import integration_link
app.include_router(integration_link.router)

from routers import integration_v1_platform
app.include_router(integration_v1_platform.router)

from routers import integration_v1_content
app.include_router(integration_v1_content.router)

from routers import integration_pairing
app.include_router(integration_pairing.router)

from routers import kanban
app.include_router(kanban.router)

from routers import integration_v1_docs
app.include_router(integration_v1_docs.router)


@app.get("/")
def read_root():
    return {"status": "ok", "message": "Acten Backend está corriendo"}
