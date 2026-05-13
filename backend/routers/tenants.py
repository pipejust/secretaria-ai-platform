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
from pydantic import BaseModel, Field
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
        description="Logo completo (wordmark+monograma) como data URL"
    )
    icon_data_url: Optional[str] = Field(
        None, max_length=4 * 1024 * 1024,
        description="Imagologo / icono cuadrado como data URL"
    )


class TenantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    domain: Optional[str] = Field(None, max_length=240)
    is_active: Optional[bool] = None


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

    # Crear tenant — branding inicial usa la paleta Acten (navy/teal/gold).
    # Si el super-admin subió logo/icono al crear la empresa, los embebemos
    # como data URLs desde ya — quedan listos sin que el admin del tenant
    # tenga que entrar a /admin/branding para subirlos manualmente.
    initial_branding: dict[str, Any] = {
        "company_name": payload.name.strip(),
        "primary_color": "#1F2A52",
        "secondary_color": "#3D6B5E",
        "accent_color": "#C8993B",
    }
    if payload.logo_data_url and _is_valid_data_url(payload.logo_data_url):
        initial_branding["logo_data_url"] = payload.logo_data_url
    if payload.icon_data_url and _is_valid_data_url(payload.icon_data_url):
        initial_branding["icon_data_url"] = payload.icon_data_url

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
    Best-effort: si falla, se loggea pero NO rompe la creación del tenant."""
    from sqlmodel import Session as _Session
    from database import engine as _engine
    from services.email_service import EmailService

    try:
        with _Session(_engine) as _db:
            user = _db.get(User, user_id)
            if not user:
                logger.warning("tenant welcome email: user_id=%s no existe", user_id)
                return
            role_name = user.role.name if user.role else "Administrador"
            svc = EmailService(db=_db, tenant_id=tenant_id)
            await svc.send_welcome_email(
                to_email=user.email,
                user_name=user.full_name or user.email,
                role=role_name,
            )
            logger.info(
                "tenant welcome email enviado a %s (tenant_id=%s)",
                user.email, tenant_id,
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
    cnt = db.exec(select(User).where(User.tenant_id == t.id)).all()
    return TenantOut.from_db(t, len(cnt))


@router.delete("/{slug}")
def delete_tenant(
    slug: str,
    db: Session = Depends(get_session),
    _su: User = Depends(require_superadmin),
):
    """Soft-delete: marca como inactivo. Los usuarios del tenant ya no podrán
    loguear (login chequea is_active). Para borrado físico — manualmente vía SQL.
    """
    t = db.exec(select(Tenant).where(Tenant.slug == slug.lower())).first()
    if not t:
        raise HTTPException(status_code=404, detail="Empresa no encontrada")
    if t.slug == "acten":
        raise HTTPException(status_code=400, detail="No se puede desactivar el tenant principal 'acten'.")
    t.is_active = False
    db.add(t)
    db.commit()
    return {"status": "deactivated", "slug": slug}
