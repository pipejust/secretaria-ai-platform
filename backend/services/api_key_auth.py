"""Sprint 11 — Middleware/dependency para autenticar requests vía X-API-Key.

Uso desde un router:
    from services.api_key_auth import api_key_user
    @router.get("/api/public/whatever")
    def endpoint(user: User = Depends(api_key_user)):
        ...
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Optional

from fastapi import Header, HTTPException, status
from sqlmodel import Session, select

from database import engine
from models import ApiKey, User

logger = logging.getLogger(__name__)


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def api_key_user(x_api_key: Optional[str] = Header(default=None)) -> User:
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header.")
    hashed = _hash(x_api_key)
    with Session(engine) as db:
        row = db.exec(select(ApiKey).where(ApiKey.hashed_key == hashed)).first()
        if not row or row.revoked_at:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida o revocada.")
        # Update last_used_at (best effort)
        try:
            row.last_used_at = datetime.now().isoformat()
            db.add(row); db.commit()
        except Exception:
            db.rollback()
        user = db.get(User, row.user_id)
        if not user or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Owner de la API key inactivo.")
        return user
