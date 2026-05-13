import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel
from sqlmodel import Session, select

from auth_utils import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    get_password_hash,
    verify_password,
)
from database import DEFAULT_TENANT_SLUG, get_session
from datetime import datetime
from models import Role, RoleActivity, RolePermission, Tenant, User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Autenticación"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")


class LoginRequest(BaseModel):
    username: str
    password: str
    # Multi-tenant: opcional. Si no viene, se usa el tenant default ('acten').
    # Esto preserva el flujo histórico (single-tenant) intacto.
    tenant_slug: Optional[str] = None


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    role_id: int


# ----------------------------------------------------------------------------
# Tenant resolution helpers
# ----------------------------------------------------------------------------

def _resolve_tenant(db: Session, slug: Optional[str]) -> Tenant:
    """Devuelve el Tenant correspondiente al slug, o el tenant default."""
    target_slug = (slug or DEFAULT_TENANT_SLUG).strip().lower()
    tenant = db.exec(select(Tenant).where(Tenant.slug == target_slug)).first()
    if not tenant or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Empresa no encontrada o inactiva: '{target_slug}'.",
        )
    return tenant


def get_tenant_from_request(
    db: Session = Depends(get_session),
    x_tenant_slug: Optional[str] = Header(default=None, alias="X-Tenant-Slug"),
    tenant: Optional[str] = None,  # ?tenant=slug
) -> Tenant:
    """Para endpoints PÚBLICOS (sin token): resuelve el tenant desde
    `X-Tenant-Slug` o `?tenant=` o cae al default. Útil para /api/branding/.
    """
    return _resolve_tenant(db, x_tenant_slug or tenant)


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_session),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        tenant_id: Optional[int] = payload.get("tenant_id")
        if email is None or tenant_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.exec(
        select(User).where(User.email == email).where(User.tenant_id == tenant_id)
    ).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


def get_current_tenant(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_session),
) -> Tenant:
    """Devuelve el Tenant del usuario autenticado. Las queries de cualquier
    endpoint protegido deben filtrar por `tenant.id` para garantizar
    aislamiento — el usuario no puede ver datos de otra empresa.
    """
    tenant = db.get(Tenant, current_user.tenant_id)
    if not tenant or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tu empresa no está activa.",
        )
    return tenant


def require_session_writer(current_user: User = Depends(get_current_user)) -> User:
    """Permite escribir sesiones / action items / curation: admin o validator.

    Cualquier rol distinto (futuro 'viewer' o 'guest') será rechazado con 403.
    """
    role_name = (current_user.role.name if current_user.role else "").lower()
    if role_name not in ("admin", "validator"):
        raise HTTPException(
            status_code=403,
            detail="Tu rol no puede crear/editar sesiones.",
        )
    return current_user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    """Admin del tenant — gestiona usuarios, proyectos, branding, integraciones."""
    if not current_user.role or current_user.role.name != "admin":
        raise HTTPException(status_code=403, detail="Not enough privileges. Admin required.")
    return current_user


def require_superadmin(current_user: User = Depends(get_current_user)) -> User:
    """Super-admin de plataforma — sólo crea/borra TENANTS (empresas).

    NO se cruza con `admin`: un super-admin no necesariamente es admin de
    cada tenant, sólo es quien provisiona empresas nuevas.
    """
    if not current_user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Esta acción requiere permisos de super-admin de plataforma.",
        )
    return current_user


# ----------------------------------------------------------------------------
# Auth endpoints
# ----------------------------------------------------------------------------

@router.post("/login")
def login_for_access_token(
    login_req: LoginRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    tenant = _resolve_tenant(db, login_req.tenant_slug)

    # Lookup tenant-scoped: el mismo email puede existir en varios tenants.
    user = db.exec(
        select(User)
        .where(User.email == login_req.username)
        .where(User.tenant_id == tenant.id)
    ).first()
    if not user or not verify_password(login_req.password, user.hashed_password):
        try:
            from services import audit as _audit
            _audit.log(db, None, request, action="login_failed",
                       resource_type="user", resource_id=login_req.username)
        except Exception:
            pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={
            "sub": user.email,
            "role": user.role.name if user.role else "",
            "tenant_id": tenant.id,
            "tenant_slug": tenant.slug,
            "is_superadmin": user.is_superadmin,
        },
        expires_delta=access_token_expires,
    )
    # Registrar el último acceso real. Si la columna aún no existe (migración
    # pendiente) o falla, el login no se rompe — solo se queda sin actualizar.
    try:
        from datetime import datetime as _dt
        user.last_login_at = _dt.now().isoformat()
        db.add(user); db.commit()
    except Exception:
        try: db.rollback()
        except Exception: pass
    try:
        from services import audit as _audit
        _audit.log(db, user, request, action="login_ok",
                   resource_type="user", resource_id=user.id)
    except Exception:
        pass
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
    }


@router.post("/register/admin-only")
def register_user(
    user_in: UserCreate,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    """Solo un Admin del MISMO tenant puede crear usuarios nuevos."""
    existing_user = db.exec(
        select(User)
        .where(User.email == user_in.email)
        .where(User.tenant_id == admin_user.tenant_id)
    ).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered en esta empresa")

    user = User(
        tenant_id=admin_user.tenant_id,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        full_name=user_in.full_name,
        role_id=user_in.role_id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"msg": "User created successfully", "user_id": user.id}


@router.get("/me")
def read_users_me(
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "role": current_user.role.name if current_user.role else None,
        "is_superadmin": current_user.is_superadmin,
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
    }


class PasswordChangeRequest(BaseModel):
    current_password: str
    new_password: str


@router.put("/password")
def change_password(
    pass_req: PasswordChangeRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(pass_req.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="La contraseña actual es incorrecta")

    current_user.hashed_password = get_password_hash(pass_req.new_password)
    db.add(current_user)
    db.commit()
    return {"msg": "Contraseña actualizada exitosamente"}


class RoleCreate(BaseModel):
    name: str
    description: str
    permissions: Optional[list[dict]] = None
    is_active: Optional[bool] = True


class RoleUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    permissions: Optional[list[dict]] = None


# Catálogo de módulos y acciones que el sistema soporta. Se expone tanto al
# frontend (para construir el formulario de permisos) como al backend para
# validar que los permisos guardados son legales.
ROLE_CATALOG: list[dict] = [
    {"key": "resumen",        "label": "Resumen",         "actions": ["view"]},
    {"key": "reuniones",      "label": "Reuniones",       "actions": ["view", "create", "edit", "delete", "export"]},
    {"key": "proyectos",      "label": "Proyectos",       "actions": ["view", "create", "edit", "delete"]},
    {"key": "tareas",         "label": "Tareas",          "actions": ["view", "edit"]},
    {"key": "calendario",     "label": "Calendario",      "actions": ["view", "edit"]},
    {"key": "reportes",       "label": "Reportes",        "actions": ["view", "export"]},
    {"key": "plantillas",     "label": "Plantillas",      "actions": ["view", "create", "edit", "delete"]},
    {"key": "pregunta_acten", "label": "Pregunta a Acten","actions": ["view"]},
    {"key": "integraciones",  "label": "Integraciones",   "actions": ["view", "manage"]},
    {"key": "usuarios",       "label": "Usuarios",        "actions": ["view", "create", "edit", "delete"]},
    {"key": "roles",          "label": "Roles",           "actions": ["view", "create", "edit", "delete"]},
    {"key": "empresas",       "label": "Empresas",        "actions": ["view", "manage"]},
    {"key": "marca",          "label": "Marca",           "actions": ["view", "edit"]},
    {"key": "configuracion",  "label": "Configuración",   "actions": ["view", "edit"]},
]
VALID_MODULES = {m["key"]: set(m["actions"]) for m in ROLE_CATALOG}
SYSTEM_ROLE_NAMES = {"admin", "validator", "viewer"}


def _log_role_activity(
    db: Session,
    role_id: int,
    action: str,
    actor: User,
    note: str = "",
) -> None:
    """Registra una entrada en la bitácora del rol — sin levantar excepciones
    para no romper el flujo principal si algo va mal."""
    try:
        entry = RoleActivity(
            role_id=role_id,
            action=action,
            actor_user_id=actor.id if actor else None,
            actor_name=(actor.full_name or actor.email) if actor else "",
            note=note,
        )
        db.add(entry)
        db.commit()
    except Exception:
        logger.exception("No se pudo registrar actividad del rol %s", role_id)


def _apply_role_permissions(db: Session, role_id: int, permissions: list[dict]) -> int:
    """Reemplaza las permissions de un rol — borra todo y vuelve a insertar.

    `permissions` es lista de `{ module_key, action, is_granted? }`. Valida
    contra el catálogo y descarta filas inválidas.
    """
    db.exec(
        # SQLModel no tiene helper para DELETE WHERE, usamos raw via session.
        # Hacemos un select+delete porque es chiquito y mantiene el código
        # legible.
        select(RolePermission).where(RolePermission.role_id == role_id)
    )
    existing = db.exec(
        select(RolePermission).where(RolePermission.role_id == role_id)
    ).all()
    for row in existing:
        db.delete(row)

    saved = 0
    for p in permissions or []:
        mod = (p.get("module_key") or "").strip()
        act = (p.get("action") or "").strip()
        granted = bool(p.get("is_granted", True))
        if not granted:
            continue
        if mod not in VALID_MODULES:
            continue
        if act not in VALID_MODULES[mod]:
            continue
        db.add(RolePermission(role_id=role_id, module_key=mod, action=act, is_granted=True))
        saved += 1
    db.commit()
    return saved


def _serialize_role(db: Session, role: Role) -> dict:
    user_count = len(db.exec(select(User).where(User.role_id == role.id)).all())
    perms = db.exec(select(RolePermission).where(RolePermission.role_id == role.id)).all()
    modules_set = {p.module_key for p in perms if p.is_granted}
    return {
        "id": role.id,
        "name": role.name,
        "description": role.description,
        "is_active": role.is_active,
        "is_system": role.is_system,
        "created_at": role.created_at,
        "updated_at": role.updated_at,
        "user_count": user_count,
        "permissions_count": len(perms),
        "modules_count": len(modules_set),
    }


@router.get("/roles/_catalog")
def get_role_catalog(admin_user: User = Depends(require_admin)):
    """Catálogo de módulos + acciones disponibles para construir el formulario."""
    return {"modules": ROLE_CATALOG}


@router.get("/roles")
def get_roles(
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    """Solo admins pueden listar roles. Los roles son globales por ahora
    (compartidos entre tenants); las permissions sí son tenant-scoped.
    """
    roles = db.exec(select(Role)).all()
    return [_serialize_role(db, r) for r in roles]


@router.post("/roles")
def create_role(
    role_in: RoleCreate,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    existing = db.exec(select(Role).where(Role.name == role_in.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="El rol ya existe")

    new_role = Role(
        name=role_in.name.strip(),
        description=(role_in.description or "").strip(),
        is_active=role_in.is_active if role_in.is_active is not None else True,
        is_system=False,
    )
    db.add(new_role)
    db.commit()
    db.refresh(new_role)

    if role_in.permissions:
        _apply_role_permissions(db, new_role.id, role_in.permissions)

    _log_role_activity(db, new_role.id, "created", admin_user, f"Rol creado: {new_role.name}")
    return _serialize_role(db, new_role)


@router.put("/roles/{role_id}")
def update_role(
    role_id: int,
    payload: RoleUpdate,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")

    changes: list[str] = []
    if payload.name is not None and payload.name.strip() and payload.name != role.name:
        if role.is_system:
            raise HTTPException(status_code=400, detail="No puedes renombrar un rol de sistema")
        clash = db.exec(select(Role).where(Role.name == payload.name, Role.id != role_id)).first()
        if clash:
            raise HTTPException(status_code=400, detail="Ya existe un rol con ese nombre")
        changes.append(f"nombre: {role.name} → {payload.name}")
        role.name = payload.name.strip()
    if payload.description is not None and payload.description != role.description:
        changes.append("descripción actualizada")
        role.description = payload.description.strip()
    if payload.is_active is not None and payload.is_active != role.is_active:
        changes.append("activado" if payload.is_active else "desactivado")
        role.is_active = bool(payload.is_active)

    role.updated_at = datetime.now().isoformat()
    db.add(role)
    db.commit()
    db.refresh(role)

    if payload.permissions is not None:
        n = _apply_role_permissions(db, role_id, payload.permissions)
        changes.append(f"{n} permisos asignados")

    if changes:
        _log_role_activity(
            db, role_id, "updated", admin_user, "; ".join(changes)
        )
    return _serialize_role(db, role)


@router.patch("/roles/{role_id}/toggle")
def toggle_role(
    role_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    if role.is_system and role.is_active:
        raise HTTPException(status_code=400, detail="No puedes desactivar un rol de sistema")
    role.is_active = not bool(role.is_active)
    role.updated_at = datetime.now().isoformat()
    db.add(role)
    db.commit()
    _log_role_activity(
        db, role_id,
        "activated" if role.is_active else "deactivated",
        admin_user,
        f'{"Activado" if role.is_active else "Desactivado"}: {role.name}',
    )
    return _serialize_role(db, role)


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_role(
    role_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    if role.is_system:
        raise HTTPException(status_code=400, detail="No puedes eliminar un rol de sistema")
    users_with_role = db.exec(select(User).where(User.role_id == role_id)).all()
    if users_with_role:
        raise HTTPException(
            status_code=400,
            detail=f"No puedes eliminar este rol — tiene {len(users_with_role)} usuario(s) asignado(s).",
        )
    # Borramos permisos primero (no hay ON DELETE CASCADE definido).
    for p in db.exec(select(RolePermission).where(RolePermission.role_id == role_id)).all():
        db.delete(p)
    role_name = role.name
    db.delete(role)
    db.commit()
    # No registramos en activity porque la FK se rompería; lo dejamos en log.
    logger.info("Rol %s eliminado por user_id=%s", role_name, admin_user.id)
    return None


@router.get("/roles/{role_id}/permissions")
def get_role_permissions(
    role_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    perms = db.exec(select(RolePermission).where(RolePermission.role_id == role_id)).all()
    return {
        "role_id": role_id,
        "permissions": [
            {"module_key": p.module_key, "action": p.action, "is_granted": p.is_granted}
            for p in perms
        ],
    }


@router.get("/roles/{role_id}/users")
def get_role_users(
    role_id: int,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    users = db.exec(select(User).where(User.role_id == role_id)).all()
    return [
        {
            "id": u.id,
            "email": u.email,
            "full_name": u.full_name,
            "is_active": u.is_active,
        }
        for u in users
    ]


@router.get("/roles/{role_id}/activity")
def get_role_activity(
    role_id: int,
    limit: int = 10,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(status_code=404, detail="Rol no encontrado")
    rows = db.exec(
        select(RoleActivity)
        .where(RoleActivity.role_id == role_id)
        .order_by(RoleActivity.id.desc())
    ).all()
    items = []
    for r in rows[: max(1, min(50, limit))]:
        items.append({
            "id": r.id,
            "action": r.action,
            "actor_name": r.actor_name,
            "note": r.note,
            "created_at": r.created_at,
        })
    return {"role_id": role_id, "items": items}


@router.get("/roles/permissions/summary")
def get_permissions_summary(
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    """Resumen global agregado para el panel derecho de Roles."""
    perms = db.exec(select(RolePermission)).all()
    modules = {p.module_key for p in perms}
    critical_actions = {"delete", "manage"}
    critical = sum(1 for p in perms if p.action in critical_actions)
    integrations_modules = {"integraciones", "empresas", "marca", "configuracion"}
    integrations_accessible = len({p.module_key for p in perms if p.module_key in integrations_modules})
    return {
        "modules_count": len(modules),
        "permissions_total": len(perms),
        "permissions_critical": critical,
        "integrations_accessible": integrations_accessible,
        "modules_available": len(VALID_MODULES),
    }


class ForgotPasswordRequest(BaseModel):
    email: str
    tenant_slug: Optional[str] = None


@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest, db: Session = Depends(get_session)):
    from auth_utils import create_password_reset_token
    from services.email_service import EmailService

    tenant = _resolve_tenant(db, req.tenant_slug)
    user = db.exec(
        select(User)
        .where(User.email == req.email)
        .where(User.tenant_id == tenant.id)
    ).first()
    # Mensaje genérico (en español) que devolvemos en ambos casos para no
    # filtrar si el correo está o no registrado en el workspace.
    generic_response = {
        "msg": "Si el correo está registrado, recibirás un enlace para restablecer tu contraseña en los próximos minutos."
    }

    if not user:
        return generic_response

    token = create_password_reset_token(user.email)

    try:
        email_svc = EmailService(db=db, tenant_id=tenant.id)
        await email_svc.send_forgot_password_email(
            to_email=user.email,
            user_name=user.full_name,
            reset_token=token,
        )
    except Exception:
        logger.exception("Error enviando correo de recuperación a %s", user.email)

    return generic_response


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@router.post("/reset-password")
def reset_password(req: ResetPasswordRequest, db: Session = Depends(get_session)):
    from auth_utils import verify_password_reset_token, get_password_hash

    email = verify_password_reset_token(req.token)
    if not email:
        raise HTTPException(status_code=400, detail="Token inválido o expirado.")

    user = db.exec(select(User).where(User.email == email)).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    user.hashed_password = get_password_hash(req.new_password)
    db.add(user)
    db.commit()
    return {"msg": "Contraseña actualizada exitosamente."}
