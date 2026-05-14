"""Branding / White-label endpoints (per-tenant).

GET  /api/branding/                         público — resuelve tenant desde
                                            X-Tenant-Slug, ?tenant=slug, o cae al default.
GET  /api/branding/{tenant_id}/logo.png     público SIN auth — sirve el logo del
                                            tenant como binario para usar en
                                            `<img src>` de emails (Gmail/Outlook
                                            bloquean data URLs).
PUT  /api/branding/                         admin del tenant — patch parcial.
POST /api/branding/logo                     admin del tenant — sube logo (full / wordmark).
DELETE /api/branding/logo                   admin del tenant — borra logo.
POST /api/branding/icon                     admin del tenant — sube imagologo (cuadrado).
DELETE /api/branding/icon                   admin del tenant — borra imagologo.
POST /api/branding/favicon                  admin del tenant — sube favicon.
"""

from __future__ import annotations

import base64
import logging
import os
import re
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, RedirectResponse, Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from database import get_session
from models import Tenant, User
from routers.auth import get_tenant_from_request, require_admin, get_current_tenant
from services import branding_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/branding", tags=["Branding (White-label)"])

MAX_LOGO_BYTES = 2 * 1024 * 1024
ALLOWED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/svg+xml",
    "image/webp",
    "image/x-icon",
    "image/vnd.microsoft.icon",
}


class BrandingPatch(BaseModel):
    company_name: str | None = Field(None, max_length=120)
    company_tagline: str | None = Field(None, max_length=240)
    company_email: str | None = Field(None, max_length=240)
    company_address: str | None = Field(None, max_length=500)
    company_website: str | None = Field(None, max_length=240)
    company_phone: str | None = Field(None, max_length=60)
    primary_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str | None = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    # Permite borrar logo/icon enviando "" — útil para "quitar" desde la UI
    # sin tener que invocar DELETE /logo o /icon. Aceptamos data URLs para
    # casos donde el cliente prefiere PATCH+body en vez de POST multipart.
    logo_data_url: str | None = Field(None, max_length=4 * 1024 * 1024)
    icon_data_url: str | None = Field(None, max_length=4 * 1024 * 1024)


@router.get("/")
def get_branding(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_tenant_from_request),
) -> dict[str, Any]:
    """Endpoint público — el login también lee de aquí.

    Resuelve el tenant desde header `X-Tenant-Slug` o querystring `?tenant=`,
    o cae al tenant default ('acten').
    """
    return branding_service.get_branding(db, tenant.id)


# ────────────────────────────────────────────────────────────────────────────
# Logo binario (PÚBLICO sin auth) — para incrustar en emails.
#
# Los clientes de email (Gmail, Outlook, Apple Mail, etc.) BLOQUEAN
# `<img src="data:image/png;base64,...">`. Por eso necesitamos servir el
# logo como una URL HTTP normal. Este endpoint:
#
#   - decodifica el `logo_data_url` o `icon_data_url` que el admin subió
#     vía la UI (almacenado en DB como data URL),
#   - devuelve el binario con el MIME real,
#   - NO requiere auth porque tiene que ser pedido por servidores SMTP
#     externos (Gmail proxy de imágenes, etc.) que nunca van a presentar
#     credenciales.
#
# Si el tenant no tiene logo subido, hace fallback al PNG default que vive
# en `templates/assets/acten-logo-default.png`. Nunca devuelve 404 — siempre
# garantiza una imagen para que el email no muestre placeholder roto.
# ────────────────────────────────────────────────────────────────────────────

# Cache de bytes para acelerar la lectura (evita hit a DB en cada email enviado).
_LOGO_BYTES_CACHE: dict[tuple[int, str], tuple[bytes, str]] = {}
_DEFAULT_LOGO_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "templates", "assets", "acten-logo-default.png",
)
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[^;]+);base64,(?P<data>.+)$", re.DOTALL)


def _serve_default_logo() -> Response:
    if os.path.isfile(_DEFAULT_LOGO_PATH):
        return FileResponse(
            _DEFAULT_LOGO_PATH,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return Response(status_code=204)


@router.get("/{tenant_id}/logo.png", include_in_schema=False)
def get_tenant_logo_binary(
    tenant_id: int,
    db: Session = Depends(get_session),
) -> Response:
    """Devuelve el logo del tenant como imagen binaria (sin auth).
    Pensado para `<img src>` en emails. Cachea agresivamente."""
    # Validamos que el tenant exista — si no, devolvemos default (no 404
    # para evitar placeholders rotos en emails ya enviados).
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return _serve_default_logo()

    cached = _LOGO_BYTES_CACHE.get((tenant_id, "logo"))
    if cached is not None:
        body, mime = cached
        return Response(
            content=body,
            media_type=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    branding = branding_service.get_branding(db, tenant_id)
    raw = (branding.get("logo_data_url") or "").strip()

    # Si el branding apunta a una URL externa, redirigimos.
    if raw.startswith("http://") or raw.startswith("https://"):
        return RedirectResponse(url=raw, status_code=302)

    # Si es data URL, decodificamos y servimos el binario.
    m = _DATA_URL_RE.match(raw)
    if not m:
        return _serve_default_logo()
    try:
        body = base64.b64decode(m.group("data"))
        mime = m.group("mime").strip() or "image/png"
    except Exception:
        return _serve_default_logo()

    _LOGO_BYTES_CACHE[(tenant_id, "logo")] = (body, mime)
    return Response(
        content=body,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _invalidate_logo_cache(tenant_id: int) -> None:
    """Limpia el cache de bytes del logo binario cuando el admin sube
    o borra el logo. Sin esto, los emails seguirían sirviendo la imagen
    vieja durante 24h (TTL del cache HTTP)."""
    _LOGO_BYTES_CACHE.pop((tenant_id, "logo"), None)
    _LOGO_BYTES_CACHE.pop((tenant_id, "icon"), None)


@router.put("/")
def put_branding(
    patch: BrandingPatch,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    payload = {k: v for k, v in patch.model_dump().items() if v is not None}
    result = branding_service.update_branding(db, tenant.id, payload)
    if "logo_data_url" in payload or "icon_data_url" in payload:
        _invalidate_logo_cache(tenant.id)
    return result


@router.post("/logo")
async def upload_logo(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Tipo no permitido. Acepto: {sorted(ALLOWED_MIME_TYPES)}",
        )
    data = await file.read()
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Logo demasiado grande (>{MAX_LOGO_BYTES // 1024} KB).",
        )
    b64 = base64.b64encode(data).decode("ascii")
    data_url = f"data:{file.content_type};base64,{b64}"
    result = branding_service.update_branding(db, tenant.id, {"logo_data_url": data_url})
    _invalidate_logo_cache(tenant.id)
    return result


@router.delete("/logo")
def delete_logo(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    result = branding_service.update_branding(db, tenant.id, {"logo_data_url": ""})
    _invalidate_logo_cache(tenant.id)
    return result


@router.post("/icon")
async def upload_icon(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Imagologo / icono cuadrado de la marca. Mismo flujo que /logo —
    validación de mime + tamaño + persistencia como data URL en branding_json.
    """
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Tipo no permitido. Acepto: {sorted(ALLOWED_MIME_TYPES)}",
        )
    data = await file.read()
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Imagologo demasiado grande (>{MAX_LOGO_BYTES // 1024} KB).",
        )
    b64 = base64.b64encode(data).decode("ascii")
    data_url = f"data:{file.content_type};base64,{b64}"
    return branding_service.update_branding(db, tenant.id, {"icon_data_url": data_url})


@router.delete("/icon")
def delete_icon(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    return branding_service.update_branding(db, tenant.id, {"icon_data_url": ""})


@router.post("/favicon")
async def upload_favicon(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Tipo no permitido para favicon.",
        )
    data = await file.read()
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Favicon demasiado grande.",
        )
    b64 = base64.b64encode(data).decode("ascii")
    data_url = f"data:{file.content_type};base64,{b64}"
    return branding_service.update_branding(db, tenant.id, {"favicon_data_url": data_url})


class TestEmailPayload(BaseModel):
    to_email: str | None = Field(default=None, description="Si no se pasa, usa el email del usuario actual.")


@router.post("/test_email")
async def test_email(
    payload: TestEmailPayload,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Envía un correo de prueba con la marca actual del tenant.

    Útil para que el admin verifique que el branding (logo, colores, dominio)
    se ve correcto en bandejas reales sin tener que esperar a un evento real.
    """
    from services.email_service import EmailService

    target = (payload.to_email or admin.email or "").strip()
    if not target or "@" not in target:
        raise HTTPException(status_code=400, detail="Email destino inválido.")

    brand = branding_service.get_branding(db, tenant.id)
    company = brand.get("company_name") or brand.get("platform_name") or "Acten"
    primary = brand.get("primary_color") or "#223148"
    secondary = brand.get("secondary_color") or "#1B7F67"
    accent = brand.get("accent_color") or "#D9A441"
    tagline = brand.get("company_tagline") or "Inteligencia para tus reuniones"
    actor_name = admin.full_name or admin.email

    html_content = f"""
    <div style="font-family: 'Inter', Arial, sans-serif; color: #0F172A; max-width: 600px; margin: 0 auto;">
      <div style="background: linear-gradient(135deg, {primary}, {secondary}); padding: 24px; color: #fff; border-radius: 12px 12px 0 0;">
        <h1 style="margin: 0; font-size: 22px;">{company}</h1>
        <p style="margin: 4px 0 0; opacity: 0.85; font-size: 13px;">{tagline}</p>
      </div>
      <div style="background: #FFFFFF; padding: 24px; border: 1px solid #E2E8F0; border-top: 0; border-radius: 0 0 12px 12px;">
        <p>Hola <strong>{actor_name}</strong>,</p>
        <p>Este es un correo de prueba enviado desde la sección de <strong>Marca</strong>
           de Acten para confirmar que tu configuración SMTP y branding funcionan correctamente.</p>
        <div style="margin: 18px 0; padding: 14px 16px; background: #F8FAFC;
                    border-left: 3px solid {accent}; border-radius: 6px; font-size: 14px;">
          Si recibiste este correo, tu integración de correos está operativa y la marca se ve como
          esperas.
        </div>
        <p style="margin-top: 24px; font-size: 12px; color: #64748B;">
          © {company} · Enviado desde la plataforma Acten.
        </p>
      </div>
    </div>
    """

    email_service = EmailService(db=db, tenant_id=tenant.id)
    try:
        ok = await email_service._send_html_email(
            to_email=target,
            subject=f"[Prueba] Correo desde {company}",
            html_content=html_content,
        )
    except Exception as exc:
        logger.exception("Falló el envío del correo de prueba")
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo enviar el correo de prueba: {exc}",
        )

    return {
        "status": "ok" if ok else "queued",
        "to": target,
        "company": company,
        "smtp_configured": bool(email_service.api_key),
    }
