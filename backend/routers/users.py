"""Endpoints REST para administración de usuarios (Control de Accesos)."""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session, select

from database import get_session
from models import AuditLog, Role, Tenant, User
from routers.auth import get_current_tenant, get_password_hash, require_admin
import crud

router = APIRouter(prefix="/users", tags=["Gestión de Usuarios"])


# ----------------------------------------------------------------------------
# DTOs
# ----------------------------------------------------------------------------

class UserUpdatePayload(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role_id: Optional[int] = None
    is_active: Optional[bool] = None
    phone: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    password: Optional[str] = None  # opcional; si viene, se re-hashea.


def _serialize_user(u: User) -> dict:
    return {
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "is_active": u.is_active,
        "role": u.role.name if u.role else "Sin Rol",
        "role_id": u.role_id,
        "phone": getattr(u, "phone", None),
        "department": getattr(u, "department", None),
        "position": getattr(u, "position", None),
        "created_at": getattr(u, "created_at", None),
        "last_login_at": getattr(u, "last_login_at", None),
        "is_superadmin": getattr(u, "is_superadmin", False),
    }


# ----------------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------------

@router.get("")
def list_users(
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Listado de usuarios del tenant actual con todos los campos visibles."""
    users = db.exec(
        select(User).where(User.tenant_id == tenant.id).order_by(User.id)
    ).all()
    return [_serialize_user(u) for u in users]


@router.get("/{user_id}")
def get_user_detail(
    user_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Detalle completo de un usuario del tenant."""
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant.id:
        # 404 (no 403) para no filtrar existencia entre empresas.
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    return _serialize_user(user)


@router.put("/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdatePayload,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Actualiza datos editables del usuario. NO permite mover entre tenants."""
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # No permitir auto-degradarse desactivándose ni cambiando su propio rol.
    if user.id == admin_user.id:
        if payload.is_active is False:
            raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta")
        if payload.role_id and payload.role_id != admin_user.role_id:
            raise HTTPException(status_code=400, detail="No puedes cambiar tu propio rol")

    if payload.role_id is not None:
        # Validar que el rol pertenece al sistema (no se acepta un id arbitrario).
        role = db.get(Role, payload.role_id)
        if not role:
            raise HTTPException(status_code=400, detail="role_id inválido")

    if payload.email is not None:
        new_email = payload.email.strip().lower()
        if not new_email:
            raise HTTPException(status_code=400, detail="El email no puede estar vacío")
        # Unicidad por tenant.
        clash = db.exec(
            select(User)
            .where(User.tenant_id == tenant.id)
            .where(User.email == new_email)
            .where(User.id != user.id)
        ).first()
        if clash:
            raise HTTPException(status_code=409, detail="Ya existe otro usuario con ese email en esta empresa")
        user.email = new_email

    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
    if payload.role_id is not None:
        user.role_id = payload.role_id
    if payload.is_active is not None:
        user.is_active = bool(payload.is_active)
    if payload.phone is not None:
        user.phone = payload.phone.strip() or None
    if payload.department is not None:
        user.department = payload.department.strip() or None
    if payload.position is not None:
        user.position = payload.position.strip() or None

    if payload.password is not None and payload.password.strip():
        if len(payload.password) < 6:
            raise HTTPException(status_code=400, detail="La contraseña debe tener al menos 6 caracteres")
        user.hashed_password = get_password_hash(payload.password)

    db.add(user)
    db.commit()
    db.refresh(user)
    return _serialize_user(user)


@router.put("/{user_id}/status")
def toggle_user_status(
    user_id: int,
    is_active: bool,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Activar/desactivar acceso. Aislado por tenant."""
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    if user.id == admin_user.id and not is_active:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta")
    updated = crud.user.update(db, db_obj=user, obj_in={"is_active": is_active})
    return {"msg": "Status actualizado", "is_active": updated.is_active}


@router.get("/{user_id}/access-summary")
def user_access_summary(
    user_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Resumen real de acceso del usuario:
       - roles asignados (1 actual + count de role_history si existiera)
       - permisos por rol (lo que el rol define)
       - sesiones activas (count de logins en últimas 24h sin logout subsecuente)
    """
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    role_permissions = 0
    direct_permissions = 0
    roles_assigned = 1 if user.role_id else 0
    if user.role_id:
        role = db.get(Role, user.role_id)
        if role:
            # `permissions` puede venir como CSV o JSON string; contamos de forma robusta.
            raw = (getattr(role, "permissions", "") or "").strip()
            if raw:
                if raw.startswith("["):
                    import json
                    try: role_permissions = len(json.loads(raw))
                    except Exception: role_permissions = len([p for p in raw.split(",") if p.strip()])
                else:
                    role_permissions = len([p for p in raw.split(",") if p.strip()])

    # Sesiones activas = logins en últimas 24h sin un logout posterior.
    from datetime import timedelta
    cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
    last_actions = db.exec(
        select(AuditLog)
        .where(AuditLog.user_id == user.id)
        .where(AuditLog.tenant_id == tenant.id)
        .where(AuditLog.created_at >= cutoff)
        .where(AuditLog.action.in_(["login_ok", "logout"]))
        .order_by(AuditLog.created_at.desc())
    ).all()
    last_action = last_actions[0] if last_actions else None
    active_sessions = 1 if (last_action and last_action.action == "login_ok") else 0

    return {
        "roles_assigned": roles_assigned,
        "direct_permissions": direct_permissions,
        "role_permissions": role_permissions,
        "active_sessions": active_sessions,
    }


@router.get("/{user_id}/activity")
def user_activity(
    user_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
    tenant: Tenant = Depends(get_current_tenant),
    limit: int = 20,
):
    """Actividad reciente real desde AuditLog del usuario."""
    user = db.get(User, user_id)
    if not user or user.tenant_id != tenant.id:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    rows = db.exec(
        select(AuditLog)
        .where(AuditLog.user_id == user.id)
        .where(AuditLog.tenant_id == tenant.id)
        .order_by(AuditLog.created_at.desc())
        .limit(max(1, min(limit, 100)))
    ).all()

    def _classify(action: str) -> dict:
        a = (action or "").lower()
        if "login" in a and "failed" not in a:
            return {"title": "Inicio de sesión exitoso", "color": "green"}
        if "login_failed" in a:
            return {"title": "Intento de inicio fallido", "color": "orange"}
        if "logout" in a:
            return {"title": "Cierre de sesión", "color": "blue"}
        if "role" in a:
            return {"title": "Cambio de rol", "color": "orange"}
        if "permission" in a:
            return {"title": "Permisos actualizados", "color": "green"}
        if "delete" in a:
            return {"title": "Eliminación", "color": "orange"}
        if "create" in a or "register" in a:
            return {"title": "Creación de recurso", "color": "blue"}
        return {"title": action.replace("_", " ").capitalize() or "Actividad", "color": "blue"}

    out = []
    for r in rows:
        meta = _classify(r.action)
        out.append({
            "id": r.id,
            "action": r.action,
            "title": meta["title"],
            "color": meta["color"],
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "ip": r.ip,
            "user_agent": r.user_agent,
            "created_at": r.created_at,
        })
    return out
