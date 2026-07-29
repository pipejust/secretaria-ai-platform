"""Autenticación de sistemas externos vía `X-API-Key`.

Dos usos:

1. **Legacy** — `api_key_user()` devuelve el `User` dueño de la clave.

2. **Integración v1** — `require_scopes("tasks:read")` devuelve un
   `IntegrationContext` con tenant, scopes y el empleado en cuyo nombre se
   actúa (`X-On-Behalf-Of`). Es lo que consume `routers/integration_v1.py`.

   ```python
   @router.get("/tasks")
   def list_tasks(ctx: IntegrationContext = Depends(require_scopes("tasks:read"))):
       ...
   ```

Ver `docs/INTEGRACION_ACTEN_RRHH.md`.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from fastapi import Depends, Header, HTTPException, status
from sqlmodel import Session, select

from database import engine, get_session
from models import ApiKey, Project, Tenant, User

logger = logging.getLogger(__name__)

# Scopes reconocidos. Una clave sin el scope pedido → 403.
KNOWN_SCOPES = frozenset({
    "sessions:read",
    "tasks:read",
    "tasks:write",
    "ask:query",
    "sync:write",
    "calendar:read",
    "calendar:write",
    # Leer el tenant entero, sin recortar por persona. Separado a
    # propósito: una clave capaz de ver todo es otra cosa que una de
    # lectura normal, y debe poder darse y quitarse sola.
    "org:read",
    "sessions:send",
    "integrations:read",
    "integrations:write",
    "outputs:read",
    "outputs:write",
    "comments:read",
    "comments:write",
    "analytics:read",
    "notifications:read",
})


def _hash(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def api_key_user(x_api_key: Optional[str] = Header(default=None)) -> User:
    """Compat: devuelve el User dueño de la clave. Sin verificación de scopes."""
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing X-API-Key header.")
    hashed = _hash(x_api_key)
    with Session(engine) as db:
        row = db.exec(select(ApiKey).where(ApiKey.hashed_key == hashed)).first()
        if not row or row.revoked_at:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida o revocada.")
        try:
            row.last_used_at = datetime.now().isoformat()
            db.add(row); db.commit()
        except Exception:
            db.rollback()
        user = db.get(User, row.user_id)
        if not user or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Owner de la API key inactivo.")
        return user


@dataclass
class IntegrationContext:
    """Contexto de una llamada externa autenticada.

    `on_behalf_of` es el UUID del empleado (en el sistema externo) en cuyo
    nombre se actúa. `visible_project_ids` son los proyectos donde ese
    empleado es miembro — la base del modelo de permisos acordado:
    *un empleado ve las reuniones de los proyectos donde es miembro*.
    """
    tenant: Tenant
    api_key: ApiKey
    scopes: frozenset[str]
    on_behalf_of: Optional[str] = None
    acting_user: Optional[User] = None
    visible_project_ids: list[int] = field(default_factory=list)
    # True cuando la llamada es «de la empresa», no de una persona: se
    # pidió con `X-On-Behalf-Of: *` y la clave tiene `org:read`. Los
    # endpoints de lectura no recortan; los que necesitan un autor real
    # (comentar, generar, enviar) siguen exigiendo una persona.
    org_wide: bool = False

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def _load_scopes(row: ApiKey) -> frozenset[str]:
    try:
        parsed = json.loads(row.scopes or "[]")
        if isinstance(parsed, list):
            return frozenset(str(s).strip() for s in parsed if str(s).strip())
    except Exception:
        logger.warning("api_key %s: scopes ilegibles, se asume vacío", row.id)
    return frozenset()


def _resolve_visibility(
    db: Session, tenant_id: int, employee_uuid: str,
) -> tuple[Optional[User], list[int]]:
    """UUID de empleado → (User de Acten, proyectos donde es miembro).

    La membresía se resuelve por `ProjectContact.external_ref`, que la
    sincronización rellena desde `ProjectMember.employee_id` del sistema
    externo.
    """
    from models import ProjectContact

    user = db.exec(
        select(User)
        .where(User.tenant_id == tenant_id)
        .where(User.external_ref == employee_uuid)
    ).first()

    rows = db.exec(
        select(Project.id)
        .join(ProjectContact, ProjectContact.project_id == Project.id)
        .where(Project.tenant_id == tenant_id)
        .where(ProjectContact.external_ref == employee_uuid)
    ).all()
    project_ids = sorted({r[0] if isinstance(r, tuple) else r for r in rows})
    return user, project_ids


def require_scopes(*required: str) -> Callable[..., IntegrationContext]:
    """Factory de dependencia que exige `X-API-Key` + los scopes indicados.

    Todos los endpoints de lectura y `PATCH` exigen además
    `X-On-Behalf-Of`. En `POST` es opcional (el sistema externo lo envía
    siempre, pero no lo hacemos obligatorio para no romper otros clientes).
    """
    needs_actor = not all(s.endswith(":write") for s in required)

    def _dep(
        x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
        x_on_behalf_of: Optional[str] = Header(default=None, alias="X-On-Behalf-Of"),
        db: Session = Depends(get_session),
    ) -> IntegrationContext:
        if not x_api_key:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta la cabecera X-API-Key.")

        row = db.exec(
            select(ApiKey).where(ApiKey.hashed_key == _hash(x_api_key))
        ).first()
        if not row or row.revoked_at:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "API key inválida o revocada.")

        tenant = db.get(Tenant, row.tenant_id)
        if not tenant or not tenant.is_active:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "La empresa no está activa.")

        scopes = _load_scopes(row)
        missing = [s for s in required if s not in scopes]
        if missing:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"La API key no tiene el alcance requerido: {', '.join(missing)}",
            )

        acting_user: Optional[User] = None
        visible: list[int] = []
        org_wide = False
        actor = (x_on_behalf_of or "").strip()

        if actor == "*":
            # Una cuenta de administración no es un empleado y no tiene
            # ficha: pedirle un UUID de persona obliga a elegir a alguien
            # al azar, o a sumar consultas persona por persona hasta
            # rozar el límite de peticiones.
            if "org:read" not in scopes:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    "X-On-Behalf-Of: * necesita el alcance 'org:read'.",
                )
            org_wide = True
        elif actor:
            acting_user, visible = _resolve_visibility(db, tenant.id, actor)
        elif needs_actor:
            if "org:read" in scopes:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Falta X-On-Behalf-Of. Usa el UUID del empleado, o '*' "
                    "para leer como empresa.",
                )
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "Falta la cabecera X-On-Behalf-Of (UUID del empleado).",
            )

        try:
            row.last_used_at = datetime.now().isoformat()
            db.add(row)
            db.commit()
        except Exception:
            db.rollback()

        return IntegrationContext(
            tenant=tenant,
            api_key=row,
            scopes=scopes,
            # En modo empresa se deja a `None` a propósito: todos los
            # endpoints ya preguntan «¿hay persona?» para decidir si
            # recortan, y así no hay que tocar ninguno.
            on_behalf_of=None if org_wide else (actor or None),
            acting_user=acting_user,
            visible_project_ids=visible,
            org_wide=org_wide,
        )

    return _dep
