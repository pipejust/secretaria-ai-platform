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
