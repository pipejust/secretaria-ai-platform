"""Landing CMS — endpoints públicos + admin.

Tres superficies:

  GET  /api/public/landing            — PÚBLICO. Devuelve el contenido del
                                        landing del tenant 'acten' (defaults
                                        merged sobre el JSON guardado).
  PUT  /api/landing-cms/              — ADMIN del tenant 'acten'. Patch parcial.
  POST /api/landing-cms/reset         — ADMIN del tenant 'acten'. Volver a defaults.
  POST /api/public/landing/contact    — PÚBLICO. Envía el formulario de
                                        contacto al email administrativo
                                        configurado en el CMS.

  GET  /api/v1/landing_page_content   — Legacy stub. Mantiene shape antiguo
                                        para clientes que aún no migran.
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, validator
from sqlmodel import Session, select

from database import DEFAULT_TENANT_SLUG, get_session
from models import Tenant, User
from routers.auth import get_current_tenant, require_admin
from services import landing_content_service


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Router 1 — público (no auth)
# ─────────────────────────────────────────────────────────────────────────────

public_router = APIRouter(prefix="/api/public", tags=["Landing (Público)"])


def _resolve_acten_tenant(db: Session) -> Tenant:
    tenant = db.exec(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).first()
    if not tenant:
        # El tenant 'acten' siempre existe en producción (lo crea el bootstrap
        # de database.py). Si no, algo grave pasó — pero igual devolvemos los
        # defaults sin escribir, para que el landing no se caiga.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant principal no disponible.",
        )
    return tenant


@public_router.get("/landing")
def get_public_landing_content(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Devuelve el contenido del landing público.

    Sin auth — lo consume el SPA que sirve acten.app sin token.
    """
    tenant = _resolve_acten_tenant(db)
    return landing_content_service.get_landing_content(db, tenant.id)


@public_router.get("/landing/people")
def get_public_landing_people(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Personas reales del sistema para la sección 'testimonios'.

    Agrega owners de action_items (gente que recibió tareas) + ProjectContacts
    (participantes registrados por el admin) del tenant 'acten'. Dedup por
    email. Si hay un User con ese email, usamos su avatar_url y full_name.

    No expone emails para evitar harvesting — solo nombre + rol + avatar.
    Limita a 8 personas para que el grid del landing quepa.
    """
    from models import ActionItem, ProjectContact, Project, User

    tenant = _resolve_acten_tenant(db)

    # 1) owners de action_items (gente que recibió tareas en sesiones reales)
    items = db.exec(
        select(ActionItem.owner_name, ActionItem.owner_email)
        .where(ActionItem.tenant_id == tenant.id)
        .where(ActionItem.owner_email != "")
    ).all()

    # 2) ProjectContacts (participantes registrados de proyectos del tenant)
    contacts = db.exec(
        select(ProjectContact.name, ProjectContact.email, ProjectContact.role)
        .join(Project, Project.id == ProjectContact.project_id)
        .where(Project.tenant_id == tenant.id)
        .where(ProjectContact.email != "")
    ).all()

    by_email: dict[str, dict[str, Any]] = {}
    for name, email in items:
        key = (email or "").lower().strip()
        if not key or key in by_email:
            continue
        by_email[key] = {"name": (name or "").strip(), "role": ""}
    for name, email, role in contacts:
        key = (email or "").lower().strip()
        if not key:
            continue
        if key in by_email:
            if not by_email[key]["role"] and role:
                by_email[key]["role"] = role
            if not by_email[key]["name"] and name:
                by_email[key]["name"] = name
        else:
            by_email[key] = {"name": (name or "").strip(), "role": (role or "").strip()}

    # 3) Avatares: si el email matchea un User, traemos su avatar_url + position.
    # API_BASE_URL para absolutizar paths relativos (/static/avatars/...) — el
    # SPA del landing corre en acten.app pero los avatares viven en api.acten.app,
    # así que sin absolutización el browser pega 404 contra acten.app/static/...
    import os
    api_base = (os.environ.get("PUBLIC_BASE_URL") or "https://api.acten.app").rstrip("/")

    def _absolutize(url: str) -> str:
        if not url:
            return ""
        if url.startswith(("http://", "https://", "data:")):
            return url
        if url.startswith("/"):
            return api_base + url
        return api_base + "/" + url

    if by_email:
        users = db.exec(
            select(User.email, User.avatar_url, User.full_name, User.position)
            .where(User.tenant_id == tenant.id)
        ).all()
        for email, avatar_url, full_name, position in users:
            key = (email or "").lower().strip()
            if key in by_email:
                if avatar_url:
                    by_email[key]["avatar_url"] = _absolutize(avatar_url)
                if full_name and (not by_email[key]["name"] or len(full_name) > len(by_email[key]["name"])):
                    by_email[key]["name"] = full_name
                if position and not by_email[key]["role"]:
                    by_email[key]["role"] = position

    def _initials(n: str) -> str:
        parts = [p for p in n.strip().split() if p]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    out: list[dict[str, Any]] = []
    for person in by_email.values():
        name = person.get("name") or "Persona"
        out.append({
            "name": name,
            "role": person.get("role") or "Equipo Acten",
            "avatar_url": person.get("avatar_url") or "",
            "initials": _initials(name),
        })

    # Prioriza los que tienen avatar (visual más rico) y limita a 8.
    out.sort(key=lambda p: (0 if p["avatar_url"] else 1, p["name"].lower()))
    return {"items": out[:8]}


@public_router.get("/landing/trust-logos")
def get_public_trust_logos(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Lista de empresas reales (tenants activos) que aparecen en la trust band.

    Sin auth. Excluye el tenant 'acten' (es la propia plataforma).
    Si el tenant tiene logo subido en branding_json, lo expone como data URL;
    si no, el frontend renderiza el name como wordmark. Cualquier campo extra
    de branding (company_name) tiene prioridad sobre el slug.
    """
    import json
    tenants = db.exec(
        select(Tenant)
        .where(Tenant.is_active == True)  # noqa: E712
        .where(Tenant.slug != DEFAULT_TENANT_SLUG)
    ).all()
    out: list[Dict[str, Any]] = []
    for t in tenants:
        brand: Dict[str, Any] = {}
        if t.branding_json:
            try:
                brand = json.loads(t.branding_json) or {}
            except (json.JSONDecodeError, TypeError):
                brand = {}
        display_name = (brand.get("company_name") or t.name or t.slug).strip()
        logo = (brand.get("logo_data_url") or "").strip()
        # Si es data URL o URL absoluta servible, la pasamos. Cualquier otro
        # valor raro queda como cadena vacía → el frontend cae a wordmark.
        if not (logo.startswith("data:") or logo.startswith("http")):
            logo = ""
        out.append({
            "slug": t.slug,
            "name": display_name,
            "logo_url": logo,
        })
    # Ordenamos: primero los que tienen logo (jerarquía visual), luego alfabético.
    out.sort(key=lambda x: (0 if x["logo_url"] else 1, x["name"].lower()))
    return {"items": out}


# ─── Contact form ───────────────────────────────────────────────────────────
#
# Rate limit naïve in-memory (por IP). En producción real conviene reemplazar
# por slowapi o Redis, pero para un form de contacto del landing alcanza con
# evitar spam burst desde un solo origen.
_CONTACT_RATE: Dict[str, list[float]] = defaultdict(list)
_CONTACT_RATE_WINDOW_SECONDS = 60 * 10  # 10 minutos
_CONTACT_RATE_MAX = 3  # máximo 3 mensajes por IP en 10 min


# Validación de email casera para evitar la dependencia `email-validator`
# (`pydantic.EmailStr`). Cubre el 99% de casos legítimos sin agregar deps.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class ContactSubmission(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=5, max_length=240)
    company: str | None = Field(default=None, max_length=120)
    role: str | None = Field(default=None, max_length=120)
    message: str = Field(min_length=10, max_length=4000)
    # Honeypot: cualquier valor no vacío en este campo invisible = bot.
    website: str | None = Field(default="")

    @validator("email")
    def _validate_email(cls, v: str) -> str:
        v = (v or "").strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("Email inválido.")
        return v.lower()


def _check_rate_limit(ip: str) -> None:
    now = time.time()
    window_start = now - _CONTACT_RATE_WINDOW_SECONDS
    hits = [t for t in _CONTACT_RATE[ip] if t >= window_start]
    if len(hits) >= _CONTACT_RATE_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Demasiados mensajes desde tu IP. Vuelve a intentar en unos minutos.",
        )
    hits.append(now)
    _CONTACT_RATE[ip] = hits


_EMAIL_HTML_TEMPLATE = """<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;max-width:600px;margin:24px auto;padding:24px;background:#f7f4ee;color:#111318;">
<h2 style="color:#223148;margin:0 0 16px;">Nuevo mensaje desde el landing</h2>
<table style="width:100%;border-collapse:collapse;background:#fff;border-radius:8px;overflow:hidden;">
  <tr><td style="padding:12px;border-bottom:1px solid #eee;"><strong>Nombre:</strong></td><td style="padding:12px;border-bottom:1px solid #eee;">{name}</td></tr>
  <tr><td style="padding:12px;border-bottom:1px solid #eee;"><strong>Email:</strong></td><td style="padding:12px;border-bottom:1px solid #eee;"><a href="mailto:{email}">{email}</a></td></tr>
  <tr><td style="padding:12px;border-bottom:1px solid #eee;"><strong>Empresa:</strong></td><td style="padding:12px;border-bottom:1px solid #eee;">{company}</td></tr>
  <tr><td style="padding:12px;border-bottom:1px solid #eee;"><strong>Cargo:</strong></td><td style="padding:12px;border-bottom:1px solid #eee;">{role}</td></tr>
</table>
<h3 style="color:#223148;margin:20px 0 8px;">Mensaje</h3>
<div style="background:#fff;padding:16px;border-radius:8px;white-space:pre-wrap;line-height:1.5;">{message}</div>
<p style="color:#687280;font-size:12px;margin-top:24px;">IP: {ip} · Origen: acten.app/#contact</p>
</body></html>"""


def _escape(text: str | None) -> str:
    if not text:
        return "—"
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )


@public_router.post("/landing/contact")
async def submit_contact_form(
    payload: ContactSubmission,
    request: Request,
    db: Session = Depends(get_session),
) -> Dict[str, Any]:
    """Recibe el form de contacto del landing y lo envía por email.

    - Honeypot anti-bot
    - Rate limit por IP
    - Email destino: `contact.email` del CMS (defaultea a hola@acten.app)
    """
    # Honeypot: si el bot rellenó el campo invisible, devolvemos 200 sin
    # hacer nada (no le damos pista de que detectamos el ataque).
    if (payload.website or "").strip():
        logger.info("contact form honeypot triggered, dropping silently")
        return {"status": "ok"}

    client_ip = (request.client.host if request.client else "unknown") or "unknown"
    _check_rate_limit(client_ip)

    tenant = _resolve_acten_tenant(db)
    content = landing_content_service.get_landing_content(db, tenant.id)
    contact_block = content.get("contact", {}) or {}
    target_email = (contact_block.get("email") or "").strip() or "hola@acten.app"

    html = _EMAIL_HTML_TEMPLATE.format(
        name=_escape(payload.name),
        email=_escape(payload.email),
        company=_escape(payload.company),
        role=_escape(payload.role),
        message=_escape(payload.message),
        ip=_escape(client_ip),
    )

    subject = f"[Contacto Landing] {payload.name} — {payload.company or 'Sin empresa'}"

    try:
        from services.email_service import EmailService
        email_service = EmailService(db=db, tenant_id=tenant.id)
        # send_html_email no levanta excepción si falla — devuelve False —
        # así que verificamos el flag.
        sent = await email_service._send_html_email(
            to_email=target_email,
            subject=subject,
            html_content=html,
        )
        if not sent:
            logger.warning("contact form email no se envió (email service inactivo).")
    except Exception:
        logger.exception("Error enviando email de contacto del landing.")
        # No tiramos 500: si el SMTP falla, igual devolvemos OK al usuario y
        # registramos para revisión. La alternativa (500) le da mala señal a
        # un visitante que sí escribió legítimamente.

    return {"status": "ok"}


# ─────────────────────────────────────────────────────────────────────────────
# Router 2 — admin (auth + rol admin + solo tenant 'acten')
# ─────────────────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/landing-cms", tags=["Landing CMS (Admin)"])


def _require_acten_tenant(tenant: Tenant = Depends(get_current_tenant)) -> Tenant:
    """Solo el admin del tenant 'acten' puede editar el contenido del landing."""
    if tenant.slug != DEFAULT_TENANT_SLUG:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Solo el tenant principal (Acten) puede editar el landing público.",
        )
    return tenant


@router.get("/")
def admin_get_landing(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Versión admin del GET — devuelve el mismo shape que /api/public/landing."""
    return landing_content_service.get_landing_content(db, tenant.id)


@router.put("/")
def admin_put_landing(
    patch: Dict[str, Any],
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Patch parcial sobre el contenido. Acepta sección a sección o el JSON
    completo — sólo las claves presentes en `patch` se sobrescriben.
    """
    if not isinstance(patch, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El payload debe ser un objeto JSON.",
        )
    return landing_content_service.update_landing_content(db, tenant.id, patch)


@router.post("/reset")
def admin_reset_landing(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(_require_acten_tenant),
) -> Dict[str, Any]:
    """Vuelve al contenido por defecto. Útil si el admin rompió algo."""
    return landing_content_service.reset_landing_content(db, tenant.id)


# ─────────────────────────────────────────────────────────────────────────────
# Router 3 — legacy stub (mantener para no romper clientes viejos)
# ─────────────────────────────────────────────────────────────────────────────

legacy_router = APIRouter(prefix="/api/v1", tags=["Landing (Legacy)"])


class _LegacyLandingContent(BaseModel):
    title: str
    description: str
    features: list[str]


@legacy_router.get("/landing_page_content", response_model=_LegacyLandingContent)
def get_legacy_landing_content(db: Session = Depends(get_session)) -> Dict[str, Any]:
    """Legacy — versión simplificada del contenido para clientes que no se
    migraron al nuevo endpoint público."""
    tenant = db.exec(
        select(Tenant).where(Tenant.slug == DEFAULT_TENANT_SLUG)
    ).first()
    if tenant:
        content = landing_content_service.get_landing_content(db, tenant.id)
    else:
        content = landing_content_service.DEFAULT_CONTENT
    return {
        "title": content.get("hero", {}).get("title", "Acten"),
        "description": content.get("hero", {}).get("subtitle", ""),
        "features": [
            (f.get("title") or "")
            for f in (content.get("features", {}).get("items") or [])
            if f.get("title")
        ],
    }
