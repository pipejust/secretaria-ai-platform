from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import List

from database import get_session
from models import User
from routers.auth import require_admin
import crud

router = APIRouter(prefix="/users", tags=["Gestión de Usuarios"])

@router.get("")
def list_users(db: Session = Depends(get_session), admin_user: User = Depends(require_admin)):
    """Lista todos los usuarios, expone email, nombre y rol pero oculta claves (solo Admin)"""
    users = crud.user.get_multi(db)
    result = []
    for u in users:
        result.append({
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "is_active": u.is_active,
            "role": u.role.name if u.role else "Sin Rol"
        })
    return result

@router.put("/{user_id}/status")
def toggle_user_status(user_id: int, is_active: bool, db: Session = Depends(get_session), admin_user: User = Depends(require_admin)):
    """Activar o desactivar usuarios (ej: cesar acceso)"""
    user = crud.user.get(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    
    # Evitar desactivarse a uno mismo
    if user.id == admin_user.id:
        raise HTTPException(status_code=400, detail="No puedes desactivar tu propia cuenta")
        
    updated_user = crud.user.update(db, db_obj=user, obj_in={"is_active": is_active})
    return {"msg": "Status actualizado", "is_active": updated_user.is_active}
