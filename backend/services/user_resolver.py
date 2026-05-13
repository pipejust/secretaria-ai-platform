"""Resolver de correos → User dentro de un tenant.

Diseño:
- El email es el ancla universal: aparece en `ActionItem.owner_email`,
  `ProjectContact.email`, `MeetingSession.processed_attendees`, calendar
  attendees, etc. Si ese email matchea un `User` del tenant, ese registro
  es la fuente de verdad para `full_name` (nombre editado por el usuario)
  y `avatar_url` (foto subida por el usuario).
- Para listados grandes, exponemos `resolve_emails()` que hace UN query
  IN (...) por tenant y devuelve un dict listo para enriquecer N filas.
- Match case-insensitive y trim — el frontend manda emails que pueden
  variar en mayúsculas/espacios.

NO crea usuarios automáticamente. Si el email no matchea, devuelve None
y el caller decide cómo mostrar (ej. "contacto externo").
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, TypedDict

from sqlmodel import Session, select

from models import Role, User

logger = logging.getLogger(__name__)


class UserSummary(TypedDict, total=False):
    id: int
    email: str
    full_name: str
    avatar_url: Optional[str]
    role: Optional[str]
    department: Optional[str]
    position: Optional[str]
    is_active: bool
    is_superadmin: bool


def _to_summary(u: User, role_name: Optional[str]) -> UserSummary:
    return {
        "id": u.id or 0,
        "email": u.email,
        "full_name": u.full_name or u.email.split("@")[0],
        "avatar_url": u.avatar_url or None,
        "role": role_name,
        "department": u.department or None,
        "position": u.position or None,
        "is_active": bool(u.is_active),
        "is_superadmin": bool(u.is_superadmin),
    }


def _normalize(email: Optional[str]) -> Optional[str]:
    if not email:
        return None
    val = email.strip().lower()
    if not val or "@" not in val:
        return None
    return val


def resolve_emails(
    db: Session,
    tenant_id: int,
    emails: List[str],
) -> Dict[str, UserSummary]:
    """Devuelve `{email_lower: UserSummary}` para los emails que matchean
    un User del tenant. Los que no matchean no aparecen en la llave."""
    cleaned = {_normalize(e) for e in (emails or [])}
    cleaned.discard(None)
    if not cleaned:
        return {}

    rows = db.exec(
        select(User, Role)
        .join(Role, Role.id == User.role_id, isouter=True)
        .where(User.tenant_id == tenant_id)
        .where(User.email.in_(list(cleaned)))  # type: ignore[attr-defined]
    ).all()

    out: Dict[str, UserSummary] = {}
    for u, r in rows:
        key = (u.email or "").strip().lower()
        if key:
            out[key] = _to_summary(u, r.name if r else None)
    return out


def _normalize_name(name: Optional[str]) -> Optional[str]:
    """Normaliza un nombre para matching: trim, lowercase, colapsa espacios.
    NO quita acentos (los nombres en español los conservan)."""
    if not name:
        return None
    parts = name.strip().lower().split()
    return " ".join(parts) if parts else None


def resolve_names_unambiguous(
    db: Session,
    tenant_id: int,
    names: List[str],
) -> Dict[str, UserSummary]:
    """Devuelve `{nombre_normalizado: UserSummary}` SOLO para nombres que
    matchean EXACTAMENTE un único usuario activo del tenant.

    Si dos usuarios del tenant comparten `full_name`, ese nombre se
    descarta — no podemos garantizar a quién corresponde, así que es más
    seguro no resolverlo que asignar la foto equivocada.

    Uso: enriquecer `processed_attendees` (que llega de Fireflies solo con
    nombres) con el email del usuario real cuando hay certeza."""
    wanted = {_normalize_name(n) for n in (names or [])}
    wanted.discard(None)
    if not wanted:
        return {}

    rows = db.exec(
        select(User, Role)
        .join(Role, Role.id == User.role_id, isouter=True)
        .where(User.tenant_id == tenant_id)
        .where(User.is_active == True)  # noqa: E712
    ).all()

    # Agrupamos por nombre normalizado para detectar colisiones.
    by_name: Dict[str, list] = {}
    for u, r in rows:
        key = _normalize_name(u.full_name)
        if not key or key not in wanted:
            continue
        by_name.setdefault(key, []).append((u, r))

    out: Dict[str, UserSummary] = {}
    for key, matches in by_name.items():
        if len(matches) == 1:
            u, r = matches[0]
            out[key] = _to_summary(u, r.name if r else None)
        # Si len(matches) >= 2 → ambiguo, no resolvemos.
    return out


def resolve_names(
    db: Session,
    tenant_id: int,
    names: List[str],
) -> Dict[str, UserSummary]:
    """Compat con el endpoint /resolve — delega a la versión segura."""
    return resolve_names_unambiguous(db, tenant_id, names)


def resolve_one(
    db: Session,
    tenant_id: int,
    email: Optional[str],
) -> Optional[UserSummary]:
    """Versión single-row. Útil cuando solo necesitamos enriquecer una fila."""
    key = _normalize(email)
    if not key:
        return None
    row = db.exec(
        select(User, Role)
        .join(Role, Role.id == User.role_id, isouter=True)
        .where(User.tenant_id == tenant_id)
        .where(User.email == key)
    ).first()
    if not row:
        return None
    u, r = row
    return _to_summary(u, r.name if r else None)


def list_directory(
    db: Session,
    tenant_id: int,
    query: Optional[str] = None,
    limit: int = 50,
) -> List[UserSummary]:
    """Listado para typeahead. Filtra por substring en email o full_name."""
    stmt = (
        select(User, Role)
        .join(Role, Role.id == User.role_id, isouter=True)
        .where(User.tenant_id == tenant_id)
        .where(User.is_active == True)  # noqa: E712
    )
    rows = db.exec(stmt.limit(max(limit * 2, 100))).all()

    q = (query or "").strip().lower()
    out: List[UserSummary] = []
    for u, r in rows:
        if q:
            blob = f"{u.email or ''} {u.full_name or ''}".lower()
            if q not in blob:
                continue
        out.append(_to_summary(u, r.name if r else None))
        if len(out) >= limit:
            break
    return out
