"""Sprint 08 — Helper para escribir audit logs.

Uso desde cualquier endpoint sensible:
    audit.log(db, user, request, action="login", resource_type="user", resource_id=user.id)
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import Request
from sqlmodel import Session

from models import AuditLog, User

logger = logging.getLogger(__name__)


def log(
    db: Session,
    user: Optional[User],
    request: Optional[Request],
    *,
    action: str,
    resource_type: Optional[str] = None,
    resource_id: Optional[str] = None,
    payload_diff: Optional[dict] = None,
) -> None:
    """Persiste una entrada de audit log. Idempotente, NO bloquea si falla."""
    try:
        ip = (request.client.host if request and request.client else None) if request else None
        ua = request.headers.get("user-agent") if request else None
        entry = AuditLog(
            user_id=user.id if user else None,
            action=action,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip=ip,
            user_agent=(ua or "")[:512],
            payload_diff=json.dumps(payload_diff, ensure_ascii=False) if payload_diff else None,
        )
        db.add(entry)
        db.commit()
    except Exception:
        logger.exception("audit.log falló (no bloqueante).")
