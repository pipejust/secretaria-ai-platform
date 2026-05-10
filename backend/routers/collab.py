"""Sprint 07 — endpoints de colaboración: versionado, comentarios y permisos.

Versionado:
    GET  /api/sessions/{id}/versions
    POST /api/sessions/{id}/versions/snapshot   — crea snapshot manual
    POST /api/sessions/{id}/restore/{version_id}

Comentarios (sticky por sección o por tarea):
    GET    /api/sessions/{id}/comments
    POST   /api/sessions/{id}/comments
    PATCH  /api/comments/{id}            — body, resolved
    DELETE /api/comments/{id}

Permisos:
    GET    /api/sessions/{id}/permissions
    POST   /api/sessions/{id}/permissions   — comparte con user_id + role
    DELETE /api/permissions/{id}
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import (
    Comment, MeetingSession, MeetingSessionVersion,
    SessionPermission, User,
)
from routers.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Colaboración (Sprint 07)"])

VALID_ROLES = {"viewer", "editor", "admin"}
VALID_SECTIONS = {"summary", "decisions", "risks", "agreements", "task", "general"}
MAX_VERSIONS_PER_SESSION = 20


def _serialize_session(s: MeetingSession) -> dict:
    """Snapshot serializable de la sesión completa."""
    return {
        "title": s.title, "date": s.date,
        "raw_summary": s.raw_summary, "raw_transcript": s.raw_transcript,
        "processed_decisions": s.processed_decisions,
        "processed_risks": s.processed_risks,
        "processed_agreements": s.processed_agreements,
        "processed_attendees": s.processed_attendees,
        "processed_themes": s.processed_themes,
        "language": s.language, "status": s.status,
        "project_id": s.project_id,
    }


def _has_access(db: Session, session_id: int, user: User, required: str = "viewer") -> bool:
    """Admin global pasa todo. Si no, busca SessionPermission del usuario."""
    if user.role and user.role.name == "admin":
        return True
    perm = db.exec(
        select(SessionPermission).where(
            SessionPermission.session_id == session_id,
            SessionPermission.user_id == user.id,
        )
    ).first()
    if not perm:
        return False
    order = {"viewer": 0, "editor": 1, "admin": 2}
    return order.get(perm.role, -1) >= order.get(required, 0)


# ============================ VERSIONADO ============================

@router.get("/api/sessions/{session_id}/versions")
def list_versions(
    session_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if not _has_access(db, session_id, user, "viewer"):
        raise HTTPException(403, "No tienes acceso a esta sesión.")
    rows = db.exec(
        select(MeetingSessionVersion)
        .where(MeetingSessionVersion.session_id == session_id)
        .order_by(MeetingSessionVersion.version_number.desc())
    ).all()
    return [
        {"id": r.id, "version_number": r.version_number,
         "edited_by_user_id": r.edited_by_user_id, "created_at": r.created_at}
        for r in rows
    ]


@router.post("/api/sessions/{session_id}/versions/snapshot")
def take_snapshot(
    session_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if not _has_access(db, session_id, user, "editor"):
        raise HTTPException(403, "Necesitas rol editor para crear snapshots.")
    sess = db.get(MeetingSession, session_id)
    if not sess:
        raise HTTPException(404, "Sesión no encontrada.")

    # Calcular siguiente version_number
    last = db.exec(
        select(MeetingSessionVersion)
        .where(MeetingSessionVersion.session_id == session_id)
        .order_by(MeetingSessionVersion.version_number.desc())
    ).first()
    next_n = (last.version_number + 1) if last else 1

    snap = MeetingSessionVersion(
        session_id=session_id, version_number=next_n,
        snapshot_json=json.dumps(_serialize_session(sess), ensure_ascii=False),
        edited_by_user_id=user.id,
    )
    db.add(snap); db.commit(); db.refresh(snap)

    # Rotar: dejar máximo MAX_VERSIONS_PER_SESSION
    all_versions = db.exec(
        select(MeetingSessionVersion)
        .where(MeetingSessionVersion.session_id == session_id)
        .order_by(MeetingSessionVersion.version_number.asc())
    ).all()
    if len(all_versions) > MAX_VERSIONS_PER_SESSION:
        for v in all_versions[: len(all_versions) - MAX_VERSIONS_PER_SESSION]:
            db.delete(v)
        db.commit()

    return {"id": snap.id, "version_number": snap.version_number,
            "created_at": snap.created_at}


@router.post("/api/sessions/{session_id}/restore/{version_id}")
def restore_version(
    session_id: int, version_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if not _has_access(db, session_id, user, "editor"):
        raise HTTPException(403, "Necesitas rol editor para restaurar versiones.")
    sess = db.get(MeetingSession, session_id)
    ver = db.get(MeetingSessionVersion, version_id)
    if not sess or not ver or ver.session_id != session_id:
        raise HTTPException(404, "Sesión o versión no encontrada.")

    # Snapshot del estado actual antes de pisar
    take_snapshot(session_id, db, user)

    payload = json.loads(ver.snapshot_json)
    for field, value in payload.items():
        if hasattr(sess, field):
            setattr(sess, field, value)
    db.add(sess); db.commit(); db.refresh(sess)
    return {"status": "restored", "version_number": ver.version_number}


# ============================ COMENTARIOS ============================

class CommentCreate(BaseModel):
    section: str
    body: str
    ref_id: Optional[int] = None
    parent_comment_id: Optional[int] = None


class CommentUpdate(BaseModel):
    body: Optional[str] = None
    resolved: Optional[bool] = None


@router.get("/api/sessions/{session_id}/comments")
def list_comments(
    session_id: int,
    section: Optional[str] = Query(None),
    include_resolved: bool = Query(True),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if not _has_access(db, session_id, user, "viewer"):
        raise HTTPException(403, "No tienes acceso a esta sesión.")
    stmt = select(Comment).where(Comment.session_id == session_id)
    if section:
        stmt = stmt.where(Comment.section == section)
    if not include_resolved:
        stmt = stmt.where(Comment.resolved_at == None)  # noqa: E711
    rows = db.exec(stmt.order_by(Comment.created_at.asc())).all()
    return [
        {"id": c.id, "section": c.section, "ref_id": c.ref_id,
         "author_user_id": c.author_user_id, "body": c.body,
         "parent_comment_id": c.parent_comment_id,
         "resolved_at": c.resolved_at, "created_at": c.created_at}
        for c in rows
    ]


@router.post("/api/sessions/{session_id}/comments")
def create_comment(
    session_id: int,
    payload: CommentCreate,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if payload.section not in VALID_SECTIONS:
        raise HTTPException(400, f"section debe ser uno de {sorted(VALID_SECTIONS)}.")
    if not _has_access(db, session_id, user, "viewer"):
        raise HTTPException(403, "No tienes acceso a esta sesión.")
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(400, "El comentario no puede estar vacío.")
    c = Comment(
        session_id=session_id, section=payload.section,
        ref_id=payload.ref_id, author_user_id=user.id,
        body=body, parent_comment_id=payload.parent_comment_id,
    )
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "created_at": c.created_at}


@router.patch("/api/comments/{comment_id}")
def update_comment(
    comment_id: int,
    payload: CommentUpdate,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    c = db.get(Comment, comment_id)
    if not c:
        raise HTTPException(404, "Comentario no encontrado.")
    if c.author_user_id != user.id and not (user.role and user.role.name == "admin"):
        raise HTTPException(403, "Solo el autor puede editar el comentario.")
    if payload.body is not None:
        c.body = payload.body
    if payload.resolved is not None:
        c.resolved_at = datetime.now().isoformat() if payload.resolved else None
    db.add(c); db.commit(); db.refresh(c)
    return {"id": c.id, "resolved_at": c.resolved_at}


@router.delete("/api/comments/{comment_id}")
def delete_comment(
    comment_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    c = db.get(Comment, comment_id)
    if not c:
        raise HTTPException(404, "Comentario no encontrado.")
    if c.author_user_id != user.id and not (user.role and user.role.name == "admin"):
        raise HTTPException(403, "Solo el autor puede borrar el comentario.")
    db.delete(c); db.commit()
    return {"status": "deleted", "id": comment_id}


# ============================ PERMISOS ============================

class PermissionGrant(BaseModel):
    user_id: int
    role: str = "viewer"


@router.get("/api/sessions/{session_id}/permissions")
def list_permissions(
    session_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if not _has_access(db, session_id, user, "admin"):
        raise HTTPException(403, "Solo admin puede ver permisos.")
    rows = db.exec(
        select(SessionPermission).where(SessionPermission.session_id == session_id)
    ).all()
    return [
        {"id": p.id, "user_id": p.user_id, "role": p.role, "granted_at": p.granted_at}
        for p in rows
    ]


@router.post("/api/sessions/{session_id}/permissions")
def grant_permission(
    session_id: int,
    payload: PermissionGrant,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    if payload.role not in VALID_ROLES:
        raise HTTPException(400, f"role debe ser uno de {sorted(VALID_ROLES)}.")
    if not _has_access(db, session_id, user, "admin"):
        raise HTTPException(403, "Solo admin puede otorgar permisos.")
    target = db.get(User, payload.user_id)
    if not target:
        raise HTTPException(404, "user_id no existe.")
    # Si ya hay permiso, lo actualiza
    existing = db.exec(
        select(SessionPermission).where(
            SessionPermission.session_id == session_id,
            SessionPermission.user_id == payload.user_id,
        )
    ).first()
    if existing:
        existing.role = payload.role
        db.add(existing); db.commit(); db.refresh(existing)
        return {"id": existing.id, "role": existing.role, "status": "updated"}
    perm = SessionPermission(
        session_id=session_id, user_id=payload.user_id, role=payload.role,
    )
    db.add(perm); db.commit(); db.refresh(perm)
    return {"id": perm.id, "role": perm.role, "status": "granted"}


@router.delete("/api/permissions/{permission_id}")
def revoke_permission(
    permission_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
):
    p = db.get(SessionPermission, permission_id)
    if not p:
        raise HTTPException(404, "Permiso no encontrado.")
    if not _has_access(db, p.session_id, user, "admin"):
        raise HTTPException(403, "Solo admin puede revocar permisos.")
    db.delete(p); db.commit()
    return {"status": "revoked", "id": permission_id}
