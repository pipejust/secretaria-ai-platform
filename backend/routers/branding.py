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
import io
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

# MIMEs sobre los que SÍ podemos autocropear (raster). SVG/ICO se dejan
# tal cual — su "padding" interno se controla por el viewBox del autor.
_RASTER_MIMES = {"image/png", "image/jpeg", "image/webp"}


def _autocrop_raster(raw: bytes, mime: str) -> tuple[bytes, str]:
    """Recorta el padding transparente/uniforme alrededor del contenido
    visible de una imagen raster. Devuelve (bytes_nuevos, mime_efectivo).

    Caso típico: un PNG de 553×237 con el wordmark real ocupando solo el
    centro 200×80 — el resto es alpha=0. Al renderizar con object-fit:
    contain en un container 320×80, el navegador escala los 553px → muy
    chico el contenido visible. Con autocrop, el PNG queda 200×80 y se
    renderiza prominente.

    Si la imagen no es raster, no tiene alpha, o el crop falla por
    cualquier motivo, devuelve los bytes originales sin tocar.
    """
    if mime not in _RASTER_MIMES:
        return raw, mime
    try:
        from PIL import Image  # import local para evitar costo en boot
    except ImportError:
        logger.warning("Pillow no instalado, salto autocrop")
        return raw, mime
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Autocrop: no pude abrir la imagen: %s", exc)
        return raw, mime

    # Para detectar el bbox del contenido, necesitamos alpha. Si el JPEG
    # no tiene alpha, convertimos a RGBA y detectamos por contraste con
    # el color de fondo más común en las esquinas (típicamente blanco).
    has_alpha = img.mode in ("RGBA", "LA") or "transparency" in img.info
    if not has_alpha:
        img = img.convert("RGBA")
        # Heurística: si el color promedio de las 4 esquinas es uniforme,
        # asumimos que es el background y lo hacemos transparente.
        corners = [img.getpixel((0, 0)), img.getpixel((img.width - 1, 0)),
                   img.getpixel((0, img.height - 1)),
                   img.getpixel((img.width - 1, img.height - 1))]
        if all(c == corners[0] for c in corners) and corners[0][3] == 255:
            bg = corners[0][:3]
            # Convertir bg a alpha=0 — pero solo si es un color "uniforme"
            # (no gradiente). Tolerancia ±5 por canal para PNGs con jpg-like noise.
            datas = list(img.getdata())
            new_data = [
                (0, 0, 0, 0)
                if all(abs(p[i] - bg[i]) <= 5 for i in range(3))
                else p
                for p in datas
            ]
            img.putdata(new_data)
    else:
        img = img.convert("RGBA") if img.mode != "RGBA" else img

    bbox = img.getbbox()
    if not bbox:
        return raw, mime  # imagen totalmente transparente, no toco
    # Si el bbox ya cubre >95% del área, no hay padding significativo.
    bw, bh = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if bw * bh >= 0.95 * img.width * img.height:
        return raw, mime
    try:
        cropped = img.crop(bbox)
        buf = io.BytesIO()
        # PNG preserva alpha — único formato seguro para logos con transparencia.
        cropped.save(buf, format="PNG", optimize=True)
        new_bytes = buf.getvalue()
        logger.info(
            "Autocrop OK: %dx%d → %dx%d (%d → %d bytes)",
            img.width, img.height, cropped.width, cropped.height,
            len(raw), len(new_bytes),
        )
        return new_bytes, "image/png"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Autocrop falló en save: %s", exc)
        return raw, mime


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
    logo_dark_data_url: str | None = Field(None, max_length=4 * 1024 * 1024)
    icon_data_url: str | None = Field(None, max_length=4 * 1024 * 1024)
    # Idioma por defecto del workspace. NO va a branding_json — se
    # persiste directo en Tenant.default_language (vivo en columna real,
    # no en blob JSON). Lo aceptamos acá para que /admin/branding sea el
    # único endpoint que tiene que llamar el admin del tenant para
    # configurar SU empresa.
    default_language: str | None = Field(None, max_length=4)


@router.get("/")
def get_branding(
    db: Session = Depends(get_session),
    tenant: Tenant = Depends(get_tenant_from_request),
) -> dict[str, Any]:
    """Endpoint público — el login también lee de aquí.

    Resuelve el tenant desde header `X-Tenant-Slug` o querystring `?tenant=`,
    o cae al tenant default ('acten').
    """
    out = branding_service.get_branding(db, tenant.id)
    # Inyectamos default_language para que el formulario de /admin/branding
    # lo pre-cargue y muestre el valor actual del workspace sin pedir un
    # endpoint extra. Default 'es' si la columna es NULL/vacía.
    out["default_language"] = (tenant.default_language or "es")
    return out


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
    _LOGO_BYTES_CACHE.pop((tenant_id, "logo_dark"), None)
    _LOGO_BYTES_CACHE.pop((tenant_id, "icon"), None)


@router.get("/{tenant_id}/logo-dark.png", include_in_schema=False)
def get_tenant_logo_dark_binary(
    tenant_id: int,
    db: Session = Depends(get_session),
) -> Response:
    """Devuelve el logo OSCURO del tenant como imagen binaria (sin auth).
    Pensado para sustituir el logo en fondos oscuros (hero de la landing,
    dark mode). Si no hay logo oscuro subido, cae al `logo_data_url`
    regular para no romper la UI."""
    tenant = db.get(Tenant, tenant_id)
    if not tenant:
        return _serve_default_logo()

    cached = _LOGO_BYTES_CACHE.get((tenant_id, "logo_dark"))
    if cached is not None:
        body, mime = cached
        return Response(
            content=body,
            media_type=mime,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    branding = branding_service.get_branding(db, tenant_id)
    raw = (branding.get("logo_dark_data_url") or "").strip()
    # Fallback al logo regular si no hay versión oscura.
    if not raw:
        raw = (branding.get("logo_data_url") or "").strip()

    if raw.startswith("http://") or raw.startswith("https://"):
        return RedirectResponse(url=raw, status_code=302)

    m = _DATA_URL_RE.match(raw)
    if not m:
        return _serve_default_logo()
    try:
        body = base64.b64decode(m.group("data"))
        mime = m.group("mime").strip() or "image/png"
    except Exception:
        return _serve_default_logo()

    _LOGO_BYTES_CACHE[(tenant_id, "logo_dark")] = (body, mime)
    return Response(
        content=body,
        media_type=mime,
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.put("/")
def put_branding(
    patch: BrandingPatch,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    payload = {k: v for k, v in patch.model_dump().items() if v is not None}

    # default_language vive en columna real (Tenant.default_language), NO
    # en branding_json. Lo sacamos del payload antes de delegar al service
    # y lo persistimos a mano. Si viene con valor inválido, 422.
    dl = payload.pop("default_language", None)
    if dl is not None:
        code = (dl or "").strip().lower()
        if code not in ("es", "ca", "en"):
            raise HTTPException(
                status_code=422,
                detail="default_language debe ser 'es', 'ca' o 'en'.",
            )
        tenant.default_language = code
        db.add(tenant)
        db.commit()
        db.refresh(tenant)

    result = branding_service.update_branding(db, tenant.id, payload)
    # Reflejamos el idioma en la respuesta para que el cliente actualice
    # su estado sin pedir un GET aparte.
    result["default_language"] = tenant.default_language or "es"
    if (
        "logo_data_url" in payload
        or "logo_dark_data_url" in payload
        or "icon_data_url" in payload
    ):
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
    data, mime = _autocrop_raster(data, file.content_type)
    b64 = base64.b64encode(data).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
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


@router.post("/logo-dark")
async def upload_logo_dark(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    """Logo OSCURO — variante de la marca diseñada para fondos oscuros
    (hero navy de la landing, dark mode). Mismo flujo que /logo:
    validación de mime + tamaño + persistencia como data URL.
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
            detail=f"Logo oscuro demasiado grande (>{MAX_LOGO_BYTES // 1024} KB).",
        )
    data, mime = _autocrop_raster(data, file.content_type)
    b64 = base64.b64encode(data).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
    result = branding_service.update_branding(
        db, tenant.id, {"logo_dark_data_url": data_url}
    )
    _invalidate_logo_cache(tenant.id)
    return result


@router.delete("/logo-dark")
def delete_logo_dark(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    result = branding_service.update_branding(
        db, tenant.id, {"logo_dark_data_url": ""}
    )
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
    data, mime = _autocrop_raster(data, file.content_type)
    b64 = base64.b64encode(data).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"
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


@router.post("/migrate_autocrop")
def migrate_autocrop_all_logos(
    db: Session = Depends(get_session),
    _admin: User = Depends(require_admin),
) -> dict[str, Any]:
    """One-shot: re-procesa TODOS los logos ya guardados en branding_json
    aplicando autocrop. Útil tras la introducción del autocrop al upload,
    para limpiar logos viejos con padding transparente (caso CCIS).

    Solo accesible a admin (no superadmin) — pero recorre todos los
    tenants. La justificación: es un endpoint de mantenimiento ejecutado
    una sola vez tras el deploy. No hay riesgo de leak entre tenants
    porque solo MODIFICA su propio branding_json.

    Devuelve el resumen: cuántos tenants procesados y cuántos crops
    efectivos por slot (logo / logo_dark / icon).
    """
    import json as _json
    from sqlmodel import select as _select

    tenants = db.exec(_select(Tenant)).all()
    summary = {
        "processed": 0,
        "logo_cropped": 0,
        "logo_dark_cropped": 0,
        "icon_cropped": 0,
        "details": [],
    }

    for t in tenants:
        if not t.branding_json:
            continue
        try:
            brand: dict[str, Any] = _json.loads(t.branding_json) or {}
        except (_json.JSONDecodeError, TypeError):
            continue
        changed_fields: list[str] = []
        for slot in ("logo_data_url", "logo_dark_data_url", "icon_data_url"):
            raw = (brand.get(slot) or "").strip()
            m = _DATA_URL_RE.match(raw)
            if not m:
                continue
            mime = m.group("mime").strip()
            try:
                blob = base64.b64decode(m.group("data"))
            except Exception:
                continue
            new_blob, new_mime = _autocrop_raster(blob, mime)
            if new_blob is blob or new_blob == blob:
                continue
            new_b64 = base64.b64encode(new_blob).decode("ascii")
            brand[slot] = f"data:{new_mime};base64,{new_b64}"
            changed_fields.append(slot)
            summary[f"{slot.replace('_data_url', '')}_cropped"] = (
                summary.get(f"{slot.replace('_data_url', '')}_cropped", 0) + 1
            )
        if changed_fields:
            t.branding_json = _json.dumps(brand, ensure_ascii=False)
            db.add(t)
            summary["processed"] += 1
            summary["details"].append({
                "tenant_id": t.id, "slug": t.slug,
                "cropped": changed_fields,
            })
            _invalidate_logo_cache(t.id)
    db.commit()
    return summary


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
    actor_name = admin.full_name or admin.email

    # Usamos el template branded `email_branding_test.html` que extiende
    # `email_base.html` — mismo header, logo, footer y .box que todos los
    # demás correos del sistema. Si esto se ve OK, el admin tiene la
    # garantía de que TODOS los correos van a verse OK (welcome,
    # action_items, session_received, 2fa, forgot_password). Antes el
    # test_email tenía un layout inline propio que NO coincidía con los
    # reales, así que "ver bien la prueba" no garantizaba nada.
    email_service = EmailService(db=db, tenant_id=tenant.id)
    template = email_service.jinja_env.get_template('email_branding_test.html')
    html_content = template.render(
        actor_name=actor_name,
        current_year=2026,
        brand=email_service.branding,  # ya tiene logo_data_url resuelto a URL HTTPS
    )
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
