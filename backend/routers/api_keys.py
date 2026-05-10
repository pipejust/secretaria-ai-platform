"""Sprint 11 — API keys CRUD para integración externa.

Las keys se devuelven en plaintext UNA SOLA VEZ al crearlas (mostrarlas
después es imposible: se guarda solo el hash). El cliente debe copiarlas
en ese momento.

Modelo de auth con header:
    curl -H "X-API-Key: nv_xxxxxxxxxxxxxxxxxxxxxxxxxxx" /api/...
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import ApiKey, User
from routers.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/me/api-keys", tags=["API Keys (Sprint 11)"])

KEY_PREFIX = "nv_"


def _hash_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _generate_key() -> tuple[str, str]:
    """Devuelve (plaintext, hashed). Plaintext sólo se muestra una vez."""
    pt = KEY_PREFIX + secrets.token_urlsafe(32)
    return pt, _hash_key(pt)


class CreateApiKeyRequest(BaseModel):
    name: str
    rate_limit_per_min: int = 60
    scopes: list[str] = []


@router.get("")
def list_my_keys(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    rows = db.exec(
        select(ApiKey).where(ApiKey.user_id == current_user.id)
    ).all()
    return [
        {"id": r.id, "name": r.name, "rate_limit_per_min": r.rate_limit_per_min,
         "scopes": r.scopes, "last_used_at": r.last_used_at,
         "revoked_at": r.revoked_at, "created_at": r.created_at}
        for r in rows
    ]


@router.post("")
def create_key(
    payload: CreateApiKeyRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    import json
    if not payload.name.strip():
        raise HTTPException(400, "name requerido.")
    pt, hashed = _generate_key()
    row = ApiKey(
        user_id=current_user.id,
        name=payload.name.strip(),
        hashed_key=hashed,
        scopes=json.dumps(payload.scopes),
        rate_limit_per_min=max(1, min(payload.rate_limit_per_min, 1000)),
    )
    db.add(row); db.commit(); db.refresh(row)
    # PLAINTEXT solo en esta respuesta. El cliente debe copiarlo ahora.
    return {
        "id": row.id, "name": row.name,
        "key": pt,                                 # << ÚNICO MOMENTO con plaintext
        "rate_limit_per_min": row.rate_limit_per_min,
        "scopes": payload.scopes,
        "warning": "Copia esta key AHORA. No se mostrará de nuevo.",
    }


@router.delete("/{key_id}")
def revoke_key(
    key_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    from datetime import datetime as _dt
    row = db.get(ApiKey, key_id)
    if not row or row.user_id != current_user.id:
        raise HTTPException(404, "API key no encontrada.")
    row.revoked_at = _dt.now().isoformat()
    db.add(row); db.commit()
    return {"status": "revoked", "id": key_id}
