"""Endpoints de notificaciones in-app.

Todos requieren auth y respetan multi-tenancy: el usuario solo ve sus
propias notificaciones del tenant activo.

GET    /api/notifications              listado paginado
GET    /api/notifications/unread_count badge del topbar
POST   /api/notifications/{id}/read    marcar una como leída + devolver link_to
POST   /api/notifications/mark_all_read marcar todas las del usuario
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlmodel import Session, func, select

from database import get_session
from models import Notification, Tenant, User
from routers.auth import get_current_tenant, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get("/")
def list_notifications(
    only_unread: bool = Query(False, description="Si true, solo no leídas"),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Lista las notificaciones del usuario (más recientes primero)."""
    q = (
        select(Notification)
        .where(Notification.tenant_id == tenant.id)
        .where(Notification.user_id == user.id)
    )
    if only_unread:
        q = q.where(Notification.is_read == False)  # noqa: E712
    q = q.order_by(Notification.id.desc()).limit(limit)

    rows = db.exec(q).all()
    unread_count = db.exec(
        select(func.count(Notification.id))
        .where(Notification.tenant_id == tenant.id)
        .where(Notification.user_id == user.id)
        .where(Notification.is_read == False)  # noqa: E712
    ).one()
    # SQLAlchemy 2 devuelve tupla en .one(); SQLModel a veces devuelve int.
    if isinstance(unread_count, tuple):
        unread_count = unread_count[0]

    return {
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "link_to": n.link_to,
                "entity_type": n.entity_type,
                "entity_id": n.entity_id,
                "is_read": n.is_read,
                "read_at": n.read_at,
                "created_at": n.created_at,
            }
            for n in rows
        ],
        "unread_count": int(unread_count or 0),
    }


@router.get("/unread_count")
def unread_count(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Solo el contador — endpoint barato que el frontend pollea cada minuto."""
    cnt = db.exec(
        select(func.count(Notification.id))
        .where(Notification.tenant_id == tenant.id)
        .where(Notification.user_id == user.id)
        .where(Notification.is_read == False)  # noqa: E712
    ).one()
    if isinstance(cnt, tuple):
        cnt = cnt[0]
    return {"unread_count": int(cnt or 0)}


@router.post("/{notif_id}/read")
def mark_as_read(
    notif_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Marca como leída UNA notificación y devuelve su link_to para que
    el frontend navegue. Idempotente: si ya estaba leída, no falla."""
    n = db.exec(
        select(Notification)
        .where(Notification.id == notif_id)
        .where(Notification.tenant_id == tenant.id)
        .where(Notification.user_id == user.id)
    ).first()
    if not n:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notificación no encontrada")

    if not n.is_read:
        n.is_read = True
        n.read_at = datetime.now().isoformat()
        db.add(n)
        db.commit()
        db.refresh(n)

    return {
        "id": n.id,
        "is_read": n.is_read,
        "read_at": n.read_at,
        "link_to": n.link_to,
    }


@router.post("/mark_all_read")
def mark_all_read(
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Marca TODAS las no-leídas del usuario como leídas. Devuelve el conteo
    afectado."""
    rows = db.exec(
        select(Notification)
        .where(Notification.tenant_id == tenant.id)
        .where(Notification.user_id == user.id)
        .where(Notification.is_read == False)  # noqa: E712
    ).all()
    now = datetime.now().isoformat()
    for n in rows:
        n.is_read = True
        n.read_at = now
        db.add(n)
    db.commit()
    return {"marked": len(rows)}
