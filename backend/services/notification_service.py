"""Servicio de notificaciones in-app.

Capa de conveniencia para que los procesos del backend (pipeline IA,
webhooks, routing externo, etc.) emitan notificaciones sin tener que
duplicar la lógica de tenant + dedup + persistencia.

Diseño:
- `notify_user(...)` para una notif a un usuario específico.
- `notify_admins(...)` para broadcast a todos los admins activos del tenant.
- `notify_owner_email(...)` cuando solo se conoce el email del owner
  (típico de tareas extraídas del LLM); resuelve a User si existe.
- Dedup ligero por (user_id, entity_type, entity_id, kind) en una ventana
  de 24h — evita spam si el mismo proceso corre varias veces sobre el
  mismo recurso.

NO bloquea: si el insert falla, se loguea y se sigue. Una notif perdida
es preferible a romper el pipeline IA.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select

from models import Notification, User

logger = logging.getLogger(__name__)


# --- Categorías canónicas ----------------------------------------------------
# Las usamos en el frontend para elegir icono/color y para que el filtro
# por kind sea estable. Si agregas una nueva, declárala aquí también.
KIND_SESSION_PROCESSED = "session_processed"
KIND_SESSION_RECEIVED = "session_received"
KIND_TASK_ASSIGNED = "task_assigned"
KIND_ROUTING_FAILED = "routing_failed"
KIND_COMMENT_MENTION = "comment_mention"
KIND_SYSTEM = "system"


def _exists_recent(
    db: Session,
    *,
    user_id: int,
    entity_type: Optional[str],
    entity_id: Optional[int],
    kind: str,
    window_hours: int = 24,
) -> bool:
    """True si ya existe una notif equivalente para el mismo usuario en las
    últimas `window_hours` horas. Evita duplicados cuando un proceso re-corre
    sobre el mismo recurso (ej. regenerar tareas)."""
    if not entity_type or entity_id is None:
        return False
    cutoff = (datetime.now() - timedelta(hours=window_hours)).isoformat()
    row = db.exec(
        select(Notification)
        .where(Notification.user_id == user_id)
        .where(Notification.entity_type == entity_type)
        .where(Notification.entity_id == entity_id)
        .where(Notification.kind == kind)
        .where(Notification.created_at >= cutoff)
    ).first()
    return row is not None


# Mapeo kind → atributo de preferencias del usuario. Si el flag está en False,
# la notificación push se suprime (la fila no se crea).
_KIND_TO_PREF: dict[str, str] = {
    KIND_SESSION_PROCESSED: "notif_session_processed",
    KIND_SESSION_RECEIVED:  "notif_session_processed",
    KIND_TASK_ASSIGNED:     "notif_task_assigned",
    KIND_ROUTING_FAILED:    "notif_security_alerts",  # alertas técnicas
    KIND_COMMENT_MENTION:   "notif_push_enabled",
    KIND_SYSTEM:            "notif_push_enabled",
}


def _user_allows_kind(db: Session, user_id: int, kind: str) -> bool:
    """Devuelve True si el usuario tiene activada la categoría correspondiente.
    Si no podemos resolver al usuario o la preferencia, asumimos True (no
    queremos perder notificaciones por un default desconocido)."""
    try:
        u = db.get(User, user_id)
        if not u:
            return True
        # Master switch: push desactivado → no se crea ninguna.
        if not getattr(u, "notif_push_enabled", True):
            return False
        pref_attr = _KIND_TO_PREF.get(kind)
        if not pref_attr:
            return True
        return bool(getattr(u, pref_attr, True))
    except Exception:
        return True


def notify_user(
    db: Session,
    *,
    tenant_id: int,
    user_id: int,
    kind: str,
    title: str,
    body: str = "",
    link_to: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    dedup: bool = True,
) -> Optional[Notification]:
    """Crea una notificación para un usuario. Retorna la fila creada o None
    si fue deduplicada, si el usuario desactivó la categoría, o si falló."""
    try:
        # Respeta las preferencias granulares del usuario.
        if not _user_allows_kind(db, user_id, kind):
            logger.debug(
                "notify_user: bloqueado por preferencias user=%s kind=%s",
                user_id, kind,
            )
            return None

        if dedup and _exists_recent(
            db,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            kind=kind,
        ):
            logger.debug(
                "notify_user: dedup hit user=%s kind=%s entity=%s/%s",
                user_id, kind, entity_type, entity_id,
            )
            return None

        n = Notification(
            tenant_id=tenant_id,
            user_id=user_id,
            kind=kind,
            title=title[:240],
            body=(body or "")[:600],
            link_to=link_to,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        db.add(n)
        db.commit()
        db.refresh(n)
        return n
    except Exception:  # noqa: BLE001
        logger.exception(
            "notify_user falló user=%s kind=%s entity=%s/%s",
            user_id, kind, entity_type, entity_id,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


def notify_admins(
    db: Session,
    *,
    tenant_id: int,
    kind: str,
    title: str,
    body: str = "",
    link_to: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> int:
    """Notifica a TODOS los admins activos del tenant. Devuelve la cantidad
    de notificaciones realmente creadas (después de dedup)."""
    admins = db.exec(
        select(User)
        .where(User.tenant_id == tenant_id)
        .where(User.is_active == True)  # noqa: E712
    ).all()
    # Filtramos por rol admin a nivel Python para no acoplarnos al modelo Role.
    # Si el User tiene `role` directo o vía `role_id`, ambos casos cuentan.
    targets: list[User] = []
    for u in admins:
        role_name = ""
        try:
            if hasattr(u, "role") and isinstance(getattr(u, "role"), str):
                role_name = getattr(u, "role") or ""
            elif u.role_id is not None:
                # Resuelve role_id → name si la fila Role existe.
                from models import Role
                r = db.get(Role, u.role_id)
                role_name = (r.name if r else "") or ""
        except Exception:
            role_name = ""
        if role_name.lower() == "admin" or u.is_superadmin:
            targets.append(u)

    created = 0
    for u in targets:
        n = notify_user(
            db,
            tenant_id=tenant_id,
            user_id=u.id,
            kind=kind,
            title=title,
            body=body,
            link_to=link_to,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        if n is not None:
            created += 1
    return created


def notify_owner_email(
    db: Session,
    *,
    tenant_id: int,
    owner_email: Optional[str],
    kind: str,
    title: str,
    body: str = "",
    link_to: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
) -> Optional[Notification]:
    """Resuelve `owner_email` a un User del tenant y le notifica.
    Si el email no matchea ningún usuario, no notifica (el dueño puede
    ser un externo que no tiene cuenta en la plataforma)."""
    if not owner_email:
        return None
    email = owner_email.strip().lower()
    if not email or "@" not in email:
        return None
    user = db.exec(
        select(User)
        .where(User.email == email)
        .where(User.tenant_id == tenant_id)
    ).first()
    if not user:
        return None
    return notify_user(
        db,
        tenant_id=tenant_id,
        user_id=user.id,
        kind=kind,
        title=title,
        body=body,
        link_to=link_to,
        entity_type=entity_type,
        entity_id=entity_id,
    )
