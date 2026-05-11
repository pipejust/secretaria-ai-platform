"""Branding / White-label endpoints (per-tenant).

GET  /api/branding/             público — resuelve tenant desde
                                X-Tenant-Slug, ?tenant=slug, o cae al default.
PUT  /api/branding/             admin del tenant — patch parcial.
POST /api/branding/logo         admin del tenant — sube logo (full / wordmark).
DELETE /api/branding/logo       admin del tenant — borra logo.
POST /api/branding/icon         admin del tenant — sube imagologo (cuadrado).
DELETE /api/branding/icon       admin del tenant — borra imagologo.
POST /api/branding/favicon      admin del tenant — sube favicon.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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


@router.put("/")
def put_branding(
    patch: BrandingPatch,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    payload = {k: v for k, v in patch.model_dump().items() if v is not None}
    return branding_service.update_branding(db, tenant.id, payload)


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
    return branding_service.update_branding(db, tenant.id, {"logo_data_url": data_url})


@router.delete("/logo")
def delete_logo(
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
) -> dict[str, Any]:
    return branding_service.update_branding(db, tenant.id, {"logo_data_url": ""})


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
