"""Super-admin: gestión de TENANTS (empresas).

GET    /api/super/tenants            list
POST   /api/super/tenants            create + provisionar admin inicial
GET    /api/super/tenants/{slug}     detail
PUT    /api/super/tenants/{slug}     update (name, domain, is_active, branding seed)
DELETE /api/super/tenants/{slug}     soft-delete (is_active=False)
GET    /api/tenants/lookup?slug=     PÚBLICO — chequea si un slug existe
                                     y devuelve {id, slug, name}. Lo usa el
                                     login antes de mandar credenciales.

NOTAS:
- Crear un tenant también crea un User admin del mismo (email + password
  pasados en el payload). Sin esto, nadie puede entrar al tenant nuevo.
- Borrar es SOFT (is_active=False) — preserva auditoría y permite revivir.
- Los super-admin son globales (`User.is_superadmin=True`); típicamente
  vive en el tenant 'acten' por convención.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlmodel import Session, select

from auth_utils import get_password_hash
from database import get_session
from models import Role, Tenant, User
from routers.auth import require_superadmin

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Tenants (Empresas)"])

SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,30}[a-z0-9])?$")
# Valida que un string sea un data URL `data:image/<sub>;base64,...`. Lo usamos
# para sanity-check rápido antes de persistirlos en branding_json. El upload
# vía POST /api/branding/logo|icon ya valida MIME y tamaño en serio.
DATA_URL_RE = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+$")


def _is_valid_data_url(value: str) -> bool:
    return bool(value) and bool(DATA_URL_RE.match(value))


class TenantCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=32, description="URL-safe ej. 'nexura'")
    name: str = Field(min_length=2, max_length=120, description="Nombre legible")
    domain: Optional[str] = Field(None, max_length=240, description="Dominio custom opcional")
    # Validamos email manualmente para no añadir dep `email-validator`.
    admin_email: str = Field(min_length=3, max_length=240, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
                              description="Email del PRIMER admin del tenant")
    admin_password: str = Field(min_length=8, max_length=128, description="Password inicial")
    admin_full_name: str = Field(min_length=2, max_length=120)
    # Branding inicial — opcional. Si vienen, los persistimos en branding_json
    # del tenant nuevo. Aceptamos data URLs `data:image/...;base64,...` (lo que
    # ya producen los pickers del frontend) o '' para "no traer".
    # NB: validamos formato pero NO MIME — el cliente sube y el backend confía
    # en su validación previa (en /api/branding/logo sí re-validamos por upload
    # multipart). Tope: 3 MB de string base64 por logo.
    logo_data_url: Optional[str] = Field(
        None, max_length=4 * 1024 * 1024,
        description="Logo claro (wordmark+monograma) para fondos claros — data URL"
    )
    logo_dark_data_url: Optional[str] = Field(
        None, max_length=4 * 1024 * 1024,
        description="Logo oscuro — variante para fondos oscuros (landing hero, dark mode)"
    )
    icon_data_url: Optional[str] = Field(
        None, max_length=4 * 1024 * 1024,
        description="Imagologo / icono cuadrado como data URL"
    )
    favicon_data_url: Optional[str] = Field(
        None, max_length=4 * 1024 * 1024,
        description="Favicon (.ico/.png) para la pestaña del browser"
    )
    # Identidad extra de la empresa — espejo de los campos editables en
    # /admin/branding. Si vienen, se guardan en branding_json desde el
    # día 1 (no obliga al admin del tenant a entrar a configurarlos).
    company_tagline: Optional[str] = Field(None, max_length=240)
    company_email: Optional[str] = Field(None, max_length=240)
    # OBLIGATORIO — el SLD del website se usa para validar el dominio de
    # los usuarios que el admin cree. Sin esto, register_user falla con
    # 400 "configurá el sitio web primero".
    company_website: str = Field(
        ..., min_length=4, max_length=240,
        description="Sitio web (URL). El dominio valida el email de cada usuario nuevo."
    )
    company_phone: Optional[str] = Field(None, max_length=60)
    company_address: Optional[str] = Field(None, max_length=500)
    # Paleta visual editable (hex #RRGGBB). Si no vienen, se usan los
    # defaults Acten (#1F2A52 navy / #3D6B5E teal / #C8993B gold).
    primary_color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")

    @field_validator("admin_email")
    @classmethod
    def _norm_admin_email(cls, v: str) -> str:
        """Normaliza email a lowercase + trim. El user puede tipear con
        mayúsculas o espacios y queremos que el create_tenant + el
        primer login funcionen sin sorpresas."""
        return (v or "").strip().lower()

    @field_validator("slug")
    @classmethod
    def _norm_slug(cls, v: str) -> str:
        """Slugs SIEMPRE en minúsculas — la URL `/t/<slug>/` es
        case-insensitive en navegadores; guardarlos lowercase evita
        duplicados accidentales (`Nexura` vs `nexura`)."""
        return (v or "").strip().lower()


class TenantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    domain: Optional[str] = Field(None, max_length=240)
    is_active: Optional[bool] = None
    # Branding editable desde el super-admin. Si vienen, se aplican como
    # patch parcial sobre branding_json del tenant target (sin necesidad
    # de impersonar al admin del tenant).
    company_name: Optional[str] = Field(None, max_length=120)
    primary_color: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    logo_data_url: Optional[str] = Field(None, max_length=4 * 1024 * 1024)
    logo_dark_data_url: Optional[str] = Field(None, max_length=4 * 1024 * 1024)
    icon_data_url: Optional[str] = Field(None, max_length=4 * 1024 * 1024)


class TenantOut(BaseModel):
    id: int
    slug: str
    name: str
    domain: Optional[str]
    is_active: bool
    user_count: int
    created_at: str
    # Branding extraído del JSON — útil para el dashboard de super-admin.
    logo_data_url: Optional[str] = None
    logo_dark_data_url: Optional[str] = None
    icon_data_url: Optional[str] = None
    primary_color: Optional[str] = None
    company_name: Optional[str] = None

    @classmethod
    def from_db(cls, t: Tenant, user_count: int) -> "TenantOut":
        import json as _json
        try:
            brand = _json.loads(t.branding_json or "{}") if t.branding_json else {}
        except (_json.JSONDecodeError, TypeError):
            brand = {}
        return cls(
            id=t.id,
            slug=t.slug,
            name=t.name,
            domain=t.domain,
            is_active=t.is_active,
            user_count=user_count,
            created_at=t.created_at,
            logo_data_url=brand.get("logo_data_url"),
            logo_dark_data_url=brand.get("logo_dark_data_url"),
            icon_data_url=brand.get("icon_data_url"),
            primary_color=brand.get("primary_color"),
            company_name=brand.get("company_name"),
        )


# ============================================================================
# PUBLIC lookup — el login lo usa antes de mandar credenciales
# ============================================================================

public_router = APIRouter(prefix="/api/tenants", tags=["Tenants (Empresas)"])


@public_router.get("/lookup")
def lookup_tenant(slug: str, db: Session = Depends(get_session)):
    """Chequea si existe un tenant. Devuelve mínimos públicos (id, slug, name).
    No filtra is_active=False para que el login pueda dar mensaje claro.
    """
    target = (slug or "").strip().lower()
    if not target:
        raise HTTPException(status_code=400, detail="slug requerido")
    t = db.exec(select(Tenant).where(Tenant.slug == target)).first()
    if not t:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    return {"id": t.id, "slug": t.slug, "name": t.name, "is_active": t.is_active}


# ============================================================================
# SUPER-ADMIN endpoints
# ============================================================================

router.prefix = "/api/super/tenants"


@router.get("/")
def list_tenants(
    db: Session = Depends(get_session),
    _su: User = Depends(require_superadmin),
):
    rows = db.exec(select(Tenant).order_by(Tenant.id.asc())).all()
    out = []
    for t in rows:
        cnt = db.exec(select(User).where(User.tenant_id == t.id)).all()
        out.append(TenantOut.from_db(t, len(cnt)))
    return out


@router.get("/{slug}")
def get_tenant(
    slug: str,
    db: Session = Depends(get_session),
    _su: User = Depends(require_superadmin),
):
    t = db.exec(select(Tenant).where(Tenant.slug == slug.lower())).first()
    if not t:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    cnt = db.exec(select(User).where(User.tenant_id == t.id)).all()
    return TenantOut.from_db(t, len(cnt))


@router.post("/", status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
    _su: User = Depends(require_superadmin),
):
    slug = payload.slug.strip().lower()
    if not SLUG_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail="Slug inválido. Usa minúsculas, números o guiones (2–32 chars).",
        )
    if db.exec(select(Tenant).where(Tenant.slug == slug)).first():
        raise HTTPException(status_code=409, detail=f"Ya existe un tenant con slug '{slug}'.")
    if payload.domain and db.exec(select(Tenant).where(Tenant.domain == payload.domain)).first():
        raise HTTPException(status_code=409, detail="Ese dominio ya está mapeado a otro tenant.")

    # Crear tenant — branding inicial usa la paleta Acten (navy/teal/gold)
    # como fallback de los 3 colores, sobreescribibles desde el payload.
    # Si el super-admin subió logos/icono al crear, los embebemos como data
    # URLs desde ya — quedan listos sin que el admin del tenant tenga que
    # entrar a /admin/branding para subirlos manualmente.
    initial_branding: dict[str, Any] = {
        "company_name": payload.name.strip(),
        "company_website": (payload.company_website or "").strip(),
        "primary_color": payload.primary_color or "#1F2A52",
        "secondary_color": payload.secondary_color or "#3D6B5E",
        "accent_color": payload.accent_color or "#C8993B",
    }
    # Texto / contacto opcional — solo se persisten si vinieron con valor.
    if payload.company_tagline:
        initial_branding["company_tagline"] = payload.company_tagline.strip()
    if payload.company_email:
        initial_branding["company_email"] = payload.company_email.strip()
    if payload.company_phone:
        initial_branding["company_phone"] = payload.company_phone.strip()
    if payload.company_address:
        initial_branding["company_address"] = payload.company_address.strip()
    # Assets gráficos — validados por _is_valid_data_url para evitar basura.
    if payload.logo_data_url and _is_valid_data_url(payload.logo_data_url):
        initial_branding["logo_data_url"] = payload.logo_data_url
    if payload.logo_dark_data_url and _is_valid_data_url(payload.logo_dark_data_url):
        initial_branding["logo_dark_data_url"] = payload.logo_dark_data_url
    if payload.icon_data_url and _is_valid_data_url(payload.icon_data_url):
        initial_branding["icon_data_url"] = payload.icon_data_url
    if payload.favicon_data_url and _is_valid_data_url(payload.favicon_data_url):
        initial_branding["favicon_data_url"] = payload.favicon_data_url

    tenant = Tenant(
        slug=slug,
        name=payload.name.strip(),
        domain=(payload.domain or None),
        branding_json=json.dumps(initial_branding, ensure_ascii=False),
        is_active=True,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    # Provisionar admin inicial. Usa el rol 'admin' (compartido entre tenants).
    admin_role = db.exec(select(Role).where(Role.name == "admin")).first()
    if not admin_role:
        admin_role = Role(name="admin", description="Administrador del tenant")
        db.add(admin_role)
        db.commit()
        db.refresh(admin_role)

    user = User(
        tenant_id=tenant.id,
        email=str(payload.admin_email).lower(),
        hashed_password=get_password_hash(payload.admin_password),
        full_name=payload.admin_full_name.strip(),
        role_id=admin_role.id,
        is_superadmin=False,
    )
    db.add(user)

    # Seed por defecto: IntegrationSettings vacíos para que /admin/settings
    # del nuevo tenant tenga formularios listos para llenar.
    from models import IntegrationSetting
    defaults = [
        ("smtp", {"provider": "Resend", "apiKey": "", "senderEmail": "no-reply@acten.local"}),
        ("fireflies", {"apiKey": ""}),
        ("trello", {"api_key": "", "token": "", "isActive": False}),
        ("jira", {"email": "", "api_token": "", "domain": "", "isActive": False}),
        ("clickup", {"api_token": "", "isActive": False}),
        ("azure_devops", {"organization": "", "project": "", "pat": "", "isActive": False}),
        ("autoCuration", {"isEnabled": False, "timeoutHours": 24}),
    ]
    for name, cfg in defaults:
        db.add(IntegrationSetting(
            tenant_id=tenant.id,
            provider_name=name,
            config_json=json.dumps(cfg),
            is_active=True,
        ))
    db.commit()

    logger.info("Tenant '%s' creado con admin %s", slug, payload.admin_email)

    # Email de bienvenida al admin recién creado — best-effort en background.
    # Lleva el branding del NUEVO tenant (logo + colores que el super-admin
    # subió al crear la empresa). Si SMTP del nuevo tenant no está configurado,
    # cae al SMTP del tenant 'acten' como fallback (resuelve dentro de
    # EmailService).
    background_tasks.add_task(
        _send_tenant_welcome_safe,
        user_id=user.id,
        tenant_id=tenant.id,
    )

    return TenantOut.from_db(tenant, 1)


async def _send_tenant_welcome_safe(user_id: int, tenant_id: int) -> None:
    """Envía email de bienvenida al admin del tenant recién provisionado.
    Best-effort: si falla, se loggea pero NO rompe la creación del tenant.

    El correo incluye la URL de acceso tenant-específica:
    https://admin.acten.app/t/{slug}/login para que el admin la guarde
    en favoritos y entre directo al login de su empresa.
    """
    import os
    from sqlmodel import Session as _Session
    from database import engine as _engine
    from services.email_service import EmailService

    try:
        with _Session(_engine) as _db:
            user = _db.get(User, user_id)
            tenant = _db.get(Tenant, tenant_id)
            if not user or not tenant:
                logger.warning(
                    "tenant welcome email: user_id=%s tenant_id=%s no existen",
                    user_id, tenant_id,
                )
                return
            role_name = user.role.name if user.role else "Administrador"

            # URL tenant-específica. ADMIN_BASE_URL puede setearse a
            # https://admin.acten.app en producción; default al frontend
            # actual (compat con dev).
            admin_base = (
                os.environ.get("ADMIN_BASE_URL")
                or os.environ.get("FRONTEND_URL")
                or "http://localhost:4200"
            ).rstrip("/")
            tenant_login_url = f"{admin_base}/t/{tenant.slug}/login"

            svc = EmailService(db=_db, tenant_id=tenant_id)
            await svc.send_welcome_email(
                to_email=user.email,
                user_name=user.full_name or user.email,
                role=role_name,
                login_url=tenant_login_url,
            )
            logger.info(
                "tenant welcome email enviado a %s (tenant_id=%s, url=%s)",
                user.email, tenant_id, tenant_login_url,
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("tenant welcome email FALLÓ user=%s tenant=%s: %s",
                         user_id, tenant_id, exc)


@router.put("/{slug}")
def update_tenant(
    slug: str,
    patch: TenantUpdate,
    db: Session = Depends(get_session),
    _su: User = Depends(require_superadmin),
):
    from services import branding_service  # local import: evita ciclo

    t = db.exec(select(Tenant).where(Tenant.slug == slug.lower())).first()
    if not t:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if patch.name is not None:
        t.name = patch.name.strip()
    if patch.domain is not None:
        new_domain = patch.domain.strip() or None
        if new_domain:
            clash = db.exec(
                select(Tenant).where(Tenant.domain == new_domain).where(Tenant.id != t.id)
            ).first()
            if clash:
                raise HTTPException(status_code=409, detail="Ese dominio ya está en uso.")
        t.domain = new_domain
    if patch.is_active is not None:
        t.is_active = patch.is_active
    db.add(t)
    db.commit()
    db.refresh(t)

    # Branding como patch parcial sobre branding_json del tenant target.
    # Sólo enviamos los campos que vinieron en el body para no pisar el
    # resto del branding (colores, contacto, taglines).
    branding_fields = {
        k: v
        for k, v in {
            "company_name": patch.company_name,
            "primary_color": patch.primary_color,
            "logo_data_url": patch.logo_data_url,
            "logo_dark_data_url": patch.logo_dark_data_url,
            "icon_data_url": patch.icon_data_url,
        }.items()
        if v is not None
    }
    if branding_fields:
        branding_service.update_branding(db, t.id, branding_fields)
        # Logos cambiaron → invalidar cache HTTP del endpoint binario para
        # que los emails dejen de servir la imagen vieja durante 24h.
        if any(k.startswith("logo") or k == "icon_data_url" for k in branding_fields):
            try:
                from routers.branding import _invalidate_logo_cache
                _invalidate_logo_cache(t.id)
            except Exception:
                pass

    cnt = db.exec(select(User).where(User.tenant_id == t.id)).all()
    return TenantOut.from_db(t, len(cnt))


@router.delete("/{slug}")
def delete_tenant(
    slug: str,
    hard: bool = False,
    db: Session = Depends(get_session),
    _su: User = Depends(require_superadmin),
):
    """Borra una empresa. Por defecto soft (is_active=False); con `?hard=true`
    elimina físicamente la empresa Y TODOS sus datos asociados (usuarios,
    proyectos, sesiones, tareas, integraciones, logs, embeddings, etc.).

    El borrado físico es IRREVERSIBLE — no hay undo. La UI debe pedir
    confirmación explícita antes de invocarlo con hard=true.
    """
    t = db.exec(select(Tenant).where(Tenant.slug == slug.lower())).first()
    if not t:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if t.slug == "acten":
        raise HTTPException(
            status_code=400,
            detail="No se puede eliminar el tenant principal 'acten'.",
        )

    if not hard:
        t.is_active = False
        db.add(t)
        db.commit()
        return {"status": "deactivated", "slug": slug}

    # Hard delete: cascada manual respetando FKs.
    deleted = _hard_delete_tenant_cascade(db, t.id)
    return {
        "status": "deleted",
        "slug": slug,
        "rows_deleted": deleted,
    }


def _hard_delete_tenant_cascade(db: Session, tenant_id: int) -> dict:
    """Elimina TODO lo que pertenece a un tenant en orden topológico (de
    hijos a padres) para no violar foreign keys. Usa SQL crudo porque es
    significativamente más rápido y predecible que cargar relaciones via ORM.
    """
    from sqlalchemy import text

    counts: dict = {}

    def _exec(sql: str, params: dict | None = None, key: str | None = None) -> int:
        result = db.execute(text(sql), params or {})
        affected = result.rowcount or 0
        if key:
            counts[key] = counts.get(key, 0) + affected
        return affected

    # 1) IDs de sesiones, proyectos, usuarios del tenant — los necesitamos
    #    para limpiar tablas que NO tienen tenant_id directo.
    sess_ids = [r[0] for r in db.execute(
        text("SELECT id FROM meetingsession WHERE tenant_id = :t"),
        {"t": tenant_id},
    ).fetchall()]
    proj_ids = [r[0] for r in db.execute(
        text("SELECT id FROM project WHERE tenant_id = :t"),
        {"t": tenant_id},
    ).fetchall()]
    user_ids = [r[0] for r in db.execute(
        text('SELECT id FROM "user" WHERE tenant_id = :t'),
        {"t": tenant_id},
    ).fetchall()]

    sess_clause = (
        "session_id = ANY(:sess_ids)" if sess_ids else "FALSE"
    )
    proj_clause = (
        "project_id = ANY(:proj_ids)" if proj_ids else "FALSE"
    )
    user_clause = (
        "user_id = ANY(:user_ids)" if user_ids else "FALSE"
    )

    # 2) Tablas dependientes de meetingsession (cascada por session_id).
    if sess_ids:
        for table_key, sql in [
            ("embeddingchunk",       f"DELETE FROM embeddingchunk       WHERE {sess_clause}"),
            ("sessionoutput",        f"DELETE FROM sessionoutput        WHERE {sess_clause}"),
            ("meetingsessionversion",f"DELETE FROM meetingsessionversion WHERE {sess_clause}"),
            ("comment",              f"DELETE FROM comment              WHERE {sess_clause}"),
            ("sessionpermission",    f"DELETE FROM sessionpermission    WHERE {sess_clause}"),
            ("calendarevent",        f"DELETE FROM calendarevent        WHERE {sess_clause}"),
        ]:
            try:
                _exec(sql, {"sess_ids": sess_ids}, key=table_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skip %s (probablemente no existe): %s", table_key, exc)

    # 3) Tablas dependientes de project.
    if proj_ids:
        for table_key, sql in [
            ("routing",        f"DELETE FROM routing        WHERE {proj_clause}"),
            ("projectcontact", f"DELETE FROM projectcontact WHERE {proj_clause}"),
            ("template",       f"DELETE FROM template       WHERE {proj_clause}"),
        ]:
            try:
                _exec(sql, {"proj_ids": proj_ids}, key=table_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skip %s: %s", table_key, exc)

    # 4) Tablas dependientes de user.
    if user_ids:
        for table_key, sql in [
            ("calendaraccount", f"DELETE FROM calendaraccount WHERE {user_clause}"),
        ]:
            try:
                _exec(sql, {"user_ids": user_ids}, key=table_key)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Skip %s: %s", table_key, exc)

    # 5) Tablas con tenant_id directo. ORDEN IMPORTA — primero las que
    #    referencian users/projects/sessions, después las independientes.
    for table_key, sql in [
        ("actionitem",        "DELETE FROM actionitem        WHERE tenant_id = :t"),
        ("meetingsession",    "DELETE FROM meetingsession    WHERE tenant_id = :t"),
        ("notification",      "DELETE FROM notification      WHERE tenant_id = :t"),
        ("askhistory",        "DELETE FROM askhistory        WHERE tenant_id = :t"),
        ("project",           "DELETE FROM project           WHERE tenant_id = :t"),
        ("outputtemplate",    "DELETE FROM outputtemplate    WHERE tenant_id = :t"),
        ("integrationsetting","DELETE FROM integrationsetting WHERE tenant_id = :t"),
        ("apikey",            "DELETE FROM apikey            WHERE tenant_id = :t"),
        ("auditlog",          "DELETE FROM auditlog          WHERE tenant_id = :t"),
        ("user",              'DELETE FROM "user"            WHERE tenant_id = :t'),
    ]:
        try:
            _exec(sql, {"t": tenant_id}, key=table_key)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Hard-delete FALLÓ en %s para tenant %s: %s — abortando.",
                table_key, tenant_id, exc,
            )
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Error eliminando datos de la tabla '{table_key}'. "
                    f"Operación abortada — la empresa NO fue eliminada. "
                    f"Detalle: {exc}"
                ),
            ) from exc

    # 6) Finalmente la empresa.
    try:
        _exec("DELETE FROM tenant WHERE id = :t", {"t": tenant_id}, key="tenant")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo eliminar la fila tenant: {exc}",
        ) from exc

    db.commit()
    logger.info("Hard-delete tenant %s OK. Filas borradas: %s", tenant_id, counts)
    return counts
