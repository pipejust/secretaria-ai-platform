"""Endpoints del directorio de usuarios — resolución de correos.

Permite al frontend convertir cualquier email que aparezca en la plataforma
(owner de tarea, participante de reunión, contacto de proyecto, etc.) en
un perfil real cuando ese email corresponde a un User del tenant.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from database import get_session
from models import Tenant, User
from routers.auth import get_current_tenant, get_current_user
from services import user_resolver

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users/directory", tags=["Directorio de usuarios"])


class ResolveEmailsRequest(BaseModel):
    emails: List[str] = []
    # Fallback para participantes de reunión que solo traen el nombre.
    # Caso típico: Fireflies persiste attendees sin email; el frontend
    # los manda en `names` para que matcheen por `User.full_name`.
    names: List[str] = []


@router.post("/resolve")
def resolve(
    body: ResolveEmailsRequest,
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Resuelve un batch de correos → users del tenant. Devuelve un mapa
    `{email_lower: UserSummary | null}` para que el frontend tenga una
    clave por cada email solicitado (los que no matchean valen null).
    Acepta también `names` para resolver por nombre completo (case-insensitive,
    trim) cuando el email no está disponible."""
    email_result = user_resolver.resolve_emails(db, tenant.id, body.emails)
    name_result = user_resolver.resolve_names(db, tenant.id, body.names)

    users_by_email: dict = {}
    for e in body.emails or []:
        key = (e or "").strip().lower()
        if not key:
            continue
        users_by_email[key] = email_result.get(key)

    users_by_name: dict = {}
    for n in body.names or []:
        key = user_resolver._normalize_name(n)
        if not key:
            continue
        users_by_name[key] = name_result.get(key)

    return {
        "users": users_by_email,
        "users_by_name": users_by_name,
    }


@router.get("")
def directory(
    q: Optional[str] = Query(None, description="Substring para email/nombre"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_session),
    user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Listado para typeahead/autocomplete (ej. asignar tareas a un user)."""
    rows = user_resolver.list_directory(db, tenant.id, q, limit)
    return {"items": rows, "total": len(rows)}
