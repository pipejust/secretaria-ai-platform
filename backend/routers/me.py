"""Sprint 08 — endpoints GDPR: export + delete account.

GDPR Right to Data Portability + Right to Erasure (Art. 17 + 20).
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlmodel import Session, select

from database import get_session
from models import (
    ActionItem, AuditLog, Comment, MeetingSession,
    Project, ProjectContact, SessionPermission, User,
)
from routers.auth import get_current_user
from services import audit

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/me", tags=["GDPR — yo"])


@router.get("")
def me(current_user: User = Depends(get_current_user)):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role.name if current_user.role else None,
    }


@router.get("/export")
def export_my_data(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """GDPR Article 20 — Right to Data Portability.

    Devuelve un ZIP con todos los datos asociados al usuario.
    """
    audit.log(db, current_user, request, action="gdpr_export",
              resource_type="user", resource_id=current_user.id)

    # AISLAMIENTO: solo sesiones del tenant del usuario (antes contaba
    # sesiones de TODAS las empresas). Se usa solo para el conteo.
    sessions = db.exec(
        select(MeetingSession)
        .where(MeetingSession.tenant_id == current_user.tenant_id)
        .where(MeetingSession.project_id != None)  # noqa: E711
    ).all()
    actions_assigned = db.exec(
        select(ActionItem).where(ActionItem.owner_email == current_user.email)
    ).all()
    comments = db.exec(
        select(Comment).where(Comment.author_user_id == current_user.id)
    ).all()
    permissions = db.exec(
        select(SessionPermission).where(SessionPermission.user_id == current_user.id)
    ).all()
    audit_entries = db.exec(
        select(AuditLog).where(AuditLog.user_id == current_user.id)
    ).all()

    def serialize(rows):
        out = []
        for r in rows:
            d = r.model_dump() if hasattr(r, "model_dump") else r.dict()
            out.append(d)
        return out

    payload = {
        "exported_at_utc": datetime.utcnow().isoformat() + "Z",
        "user": {
            "id": current_user.id, "email": current_user.email,
            "full_name": current_user.full_name,
            "role": current_user.role.name if current_user.role else None,
        },
        "sessions_visible_count": len(sessions),
        "action_items_assigned_to_me": serialize(actions_assigned),
        "my_comments": serialize(comments),
        "my_session_permissions": serialize(permissions),
        "my_audit_log": serialize(audit_entries),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("notiva-export.json", json.dumps(payload, indent=2, ensure_ascii=False, default=str))
        zf.writestr("README.txt", (
            "Este archivo contiene tus datos personales según GDPR Art. 20.\n"
            "Para borrar tu cuenta: DELETE /api/me/account (purga real a los 30 días).\n"
        ))
    buf.seek(0)
    fname = f"notiva-export-{current_user.id}-{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}.zip"
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.delete("/account")
def request_account_deletion(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
):
    """GDPR Article 17 — Right to Erasure.

    Marca la cuenta para purga (soft delete + 30 días de gracia para revertir).
    El job de purga real lo ejecuta el cron.
    """
    audit.log(db, current_user, request, action="gdpr_delete_request",
              resource_type="user", resource_id=current_user.id)

    purge_at = (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z"
    current_user.is_active = False
    # Marcamos en full_name un sufijo para que el cron lo identifique.
    if "[gdpr_purge]" not in (current_user.full_name or ""):
        current_user.full_name = f"{current_user.full_name or ''} [gdpr_purge:{purge_at}]"
    db.add(current_user); db.commit()

    return {
        "status": "deletion_requested",
        "purge_at": purge_at,
        "instructions": "Para revertir antes del purge_at, contacta soporte.",
    }
