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
from models import Role, Tenant, User

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


@router.get("/roles")
def get_roles(
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    """Solo admins pueden listar roles. Los roles son globales por ahora
    (compartidos entre tenants); las permissions sí son tenant-scoped.
    """
    roles = db.exec(select(Role)).all()
    return roles


@router.post("/roles")
def create_role(
    role_in: RoleCreate,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    existing = db.exec(select(Role).where(Role.name == role_in.name)).first()
    if existing:
        raise HTTPException(status_code=400, detail="El rol ya existe")

    new_role = Role(name=role_in.name, description=role_in.description)
    db.add(new_role)
    db.commit()
    db.refresh(new_role)
    return new_role


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
