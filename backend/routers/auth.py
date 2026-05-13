import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from pydantic import BaseModel, field_validator
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


def _normalize_email(v: str) -> str:
    """Normaliza email para que el matching contra DB sea case-insensitive
    y trim-safe. Los emails se guardan SIEMPRE en minúsculas (vimos
    `register_user` y `tenants.create_tenant` aplican `.lower()`), pero
    el user puede tipear "Fcortes@Softnexus.IO" o pegar con espacios.
    Sin esta normalización, el lookup `User.email == :input` fallaba con
    401 — el user pensaba "password mala" cuando era solo case mismatch.
    """
    return (v or "").strip().lower()


class LoginRequest(BaseModel):
    username: str
    password: str
    # Multi-tenant: opcional. Si no viene, se usa el tenant default ('acten').
    # Esto preserva el flujo histórico (single-tenant) intacto.
    tenant_slug: Optional[str] = None

    @field_validator("username")
    @classmethod
    def _norm_username(cls, v: str) -> str:
        return _normalize_email(v)


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    role_id: int

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str) -> str:
        return _normalize_email(v)


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

def _issue_access_token(user: User, tenant: Tenant) -> str:
    """Crea el JWT de acceso. Centralizado para que el flujo normal y el
    flujo 2FA emitan tokens idénticos."""
    return create_access_token(
        data={
            "sub": user.email,
            "role": user.role.name if user.role else "",
            "tenant_id": tenant.id,
            "tenant_slug": tenant.slug,
            "is_superadmin": user.is_superadmin,
        },
        expires_delta=timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def _login_success_payload(user: User, tenant: Tenant, request: Optional[Request], db: Session) -> dict:
    """Marca last_login + audit + devuelve el shape de respuesta unificado."""
    try:
        user.last_login_at = datetime.now().isoformat()
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
        "access_token": _issue_access_token(user, tenant),
        "token_type": "bearer",
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
    }


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

    # ── 2FA gate ────────────────────────────────────────────────
    # Si el usuario tiene 2FA activo, NO emitimos token todavía. Generamos
    # un código nuevo, lo enviamos por email y devolvemos un payload que
    # el frontend interpreta como "pídeme el código".
    if user.two_factor_enabled:
        from services import two_factor as _tf
        _tf.start_challenge(db, user, purpose="login")
        masked = user.email.split("@")[0]
        masked = masked[:2] + "***" + (masked[-1:] if len(masked) > 3 else "")
        return {
            "two_factor_required": True,
            "method": user.two_factor_method or "email",
            "email_masked": f"{masked}@{user.email.split('@')[-1]}",
            "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
        }

    return _login_success_payload(user, tenant, request, db)


class TwoFactorLoginVerify(BaseModel):
    username: str
    code: str
    tenant_slug: Optional[str] = None

    @field_validator("username")
    @classmethod
    def _norm_username(cls, v: str) -> str:
        return _normalize_email(v)


@router.post("/login/2fa-verify")
def verify_login_2fa(
    body: TwoFactorLoginVerify,
    request: Request,
    db: Session = Depends(get_session),
):
    """Completa el login cuando el usuario tiene 2FA activo. Recibe el
    código que llegó por email y devuelve el access_token real."""
    tenant = _resolve_tenant(db, body.tenant_slug)
    user = db.exec(
        select(User)
        .where(User.email == body.username)
        .where(User.tenant_id == tenant.id)
    ).first()
    if not user or not user.two_factor_enabled:
        raise HTTPException(status_code=400, detail="Sesión inválida. Reinicia el login.")

    from services import two_factor as _tf
    ok, reason = _tf.verify_code(db, user, body.code, purpose="login")
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    _tf.clear(db, user)
    return _login_success_payload(user, tenant, request, db)


@router.post("/register/admin-only")
def register_user(
    user_in: UserCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_session),
    admin_user: User = Depends(require_admin),
):
    """Solo un Admin del MISMO tenant puede crear usuarios nuevos.

    Tras crear el user, dispara un email de bienvenida en background con:
      · branding del tenant (logo, colores)
      · rol asignado
      · enlace al login de la plataforma
    El email NO bloquea la respuesta al admin: si el SMTP falla, se loggea
    pero el usuario igual queda creado.
    """
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

    # Email de bienvenida — best-effort en background.
    background_tasks.add_task(
        _send_welcome_email_safe,
        user_id=user.id,
        tenant_id=admin_user.tenant_id,
    )

    return {"msg": "User created successfully", "user_id": user.id}


async def _send_welcome_email_safe(user_id: int, tenant_id: int) -> None:
    """Envía el email de bienvenida en background. Swallow errors —
    nunca debe romper el flow de creación del user. El branding y el
    sender SMTP se resuelven dentro del EmailService desde la DB del
    tenant correspondiente."""
    from sqlmodel import Session as _Session
    from database import engine as _engine
    from services.email_service import EmailService

    try:
        with _Session(_engine) as _db:
            user = _db.get(User, user_id)
            if not user:
                logger.warning("welcome email: user_id=%s no existe", user_id)
                return
            role_name = user.role.name if user.role else "Usuario"
            svc = EmailService(db=_db, tenant_id=tenant_id)
            await svc.send_welcome_email(
                to_email=user.email,
                user_name=user.full_name or user.email,
                role=role_name,
            )
            logger.info("welcome email enviado a %s (tenant=%s)", user.email, tenant_id)
    except Exception as exc:  # noqa: BLE001
        logger.exception("welcome email FALLÓ para user_id=%s tenant=%s: %s",
                         user_id, tenant_id, exc)


def _serialize_user_profile(user: User, tenant: Tenant) -> dict:
    """Shape canónico del perfil — incluye todos los campos editables y prefs
    de notificación. Lo consumen GET /auth/me y PUT /auth/me."""
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.name if user.role else None,
        "is_superadmin": user.is_superadmin,
        "is_active": user.is_active,
        "phone": user.phone,
        "department": user.department,
        "position": user.position,
        "location": user.location,
        "bio": user.bio,
        "avatar_url": user.avatar_url,
        "created_at": user.created_at,
        "updated_at": user.updated_at,
        "last_login_at": user.last_login_at,
        "tenant": {"id": tenant.id, "slug": tenant.slug, "name": tenant.name},
        "notifications": {
            "email_enabled":           user.notif_email_enabled,
            "push_enabled":            user.notif_push_enabled,
            "meeting_reminders":       user.notif_meeting_reminders,
            "task_assigned":           user.notif_task_assigned,
            "session_processed":       user.notif_session_processed,
            "weekly_report":           user.notif_weekly_report,
            "security_alerts":         user.notif_security_alerts,
        },
        "two_factor": {
            "enabled": bool(user.two_factor_enabled),
            "method": user.two_factor_method or "email",
        },
    }


@router.get("/me")
def read_users_me(
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    return _serialize_user_profile(current_user, tenant)


class ProfileUpdateRequest(BaseModel):
    full_name:   Optional[str] = None
    phone:       Optional[str] = None
    position:    Optional[str] = None
    department:  Optional[str] = None
    location:    Optional[str] = None
    bio:         Optional[str] = None
    avatar_url:  Optional[str] = None


@router.put("/me")
def update_my_profile(
    payload: ProfileUpdateRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Actualiza los campos editables del propio perfil. Email y rol NO
    son editables aquí — el email requiere flujo verificado y el rol lo
    administra un usuario con privilegios."""
    data = payload.model_dump(exclude_unset=True)
    # Validaciones suaves.
    if "full_name" in data:
        name = (data["full_name"] or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="El nombre no puede estar vacío.")
        if len(name) > 160:
            raise HTTPException(status_code=400, detail="El nombre es demasiado largo (máx 160).")
        current_user.full_name = name
    if "phone" in data:
        current_user.phone = (data["phone"] or "").strip() or None
    if "position" in data:
        current_user.position = (data["position"] or "").strip() or None
    if "department" in data:
        current_user.department = (data["department"] or "").strip() or None
    if "location" in data:
        current_user.location = (data["location"] or "").strip() or None
    if "bio" in data:
        bio_val = (data["bio"] or "").strip()
        if len(bio_val) > 600:
            raise HTTPException(status_code=400, detail="La biografía es demasiado larga (máx 600).")
        current_user.bio = bio_val or None
    if "avatar_url" in data:
        current_user.avatar_url = (data["avatar_url"] or "").strip() or None

    current_user.updated_at = datetime.now().isoformat()
    db.add(current_user)
    db.commit()
    db.refresh(current_user)

    # Audit trail.
    try:
        from services import audit
        audit.log(db, current_user, None, action="profile_update",
                  resource_type="user", resource_id=current_user.id)
    except Exception:  # pragma: no cover
        pass

    return _serialize_user_profile(current_user, tenant)


class NotificationPrefsRequest(BaseModel):
    email_enabled:     Optional[bool] = None
    push_enabled:      Optional[bool] = None
    meeting_reminders: Optional[bool] = None
    task_assigned:     Optional[bool] = None
    session_processed: Optional[bool] = None
    weekly_report:     Optional[bool] = None
    security_alerts:   Optional[bool] = None


@router.put("/me/notifications")
def update_notification_prefs(
    payload: NotificationPrefsRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Actualiza las preferencias de notificación granulares del usuario.
    Los servicios que envían notificaciones consultan estos flags antes de
    emitir (ver `services/notification_dispatcher`)."""
    data = payload.model_dump(exclude_unset=True)
    mapping = {
        "email_enabled":      "notif_email_enabled",
        "push_enabled":       "notif_push_enabled",
        "meeting_reminders":  "notif_meeting_reminders",
        "task_assigned":      "notif_task_assigned",
        "session_processed":  "notif_session_processed",
        "weekly_report":      "notif_weekly_report",
        "security_alerts":    "notif_security_alerts",
    }
    for k, v in data.items():
        col = mapping.get(k)
        if col and v is not None:
            setattr(current_user, col, bool(v))

    current_user.updated_at = datetime.now().isoformat()
    db.add(current_user)
    db.commit()
    db.refresh(current_user)
    return _serialize_user_profile(current_user, tenant)["notifications"]


@router.post("/me/2fa/init")
def init_2fa(
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Genera y envía un código por email para activar 2FA. Si el usuario ya
    tiene 2FA activo, también es válido para re-enviar el código (refresh)."""
    from services import two_factor as _tf
    _tf.start_challenge(db, current_user, purpose="enable")
    return {
        "status": "code_sent",
        "email": current_user.email,
        "ttl_minutes": _tf.CODE_TTL_MINUTES,
    }


class TwoFactorConfirmRequest(BaseModel):
    code: str


@router.post("/me/2fa/confirm")
def confirm_2fa(
    body: TwoFactorConfirmRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Confirma el código y activa la 2FA permanentemente."""
    from services import two_factor as _tf
    ok, reason = _tf.verify_code(db, current_user, body.code, purpose="enable")
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    current_user.two_factor_enabled = True
    current_user.two_factor_method = "email"
    current_user.updated_at = datetime.now().isoformat()
    _tf.clear(db, current_user)
    db.add(current_user); db.commit(); db.refresh(current_user)

    try:
        from services import audit
        audit.log(db, current_user, None, action="2fa_enabled",
                  resource_type="user", resource_id=current_user.id)
    except Exception:
        pass
    return _serialize_user_profile(current_user, tenant)


class TwoFactorDisableRequest(BaseModel):
    password: str


@router.delete("/me/2fa")
def disable_2fa(
    body: TwoFactorDisableRequest,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Desactiva 2FA. Requiere la contraseña actual para evitar que un
    atacante con token robado la deshabilite."""
    if not verify_password(body.password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Contraseña incorrecta.")
    from services import two_factor as _tf
    current_user.two_factor_enabled = False
    current_user.updated_at = datetime.now().isoformat()
    _tf.clear(db, current_user)
    db.add(current_user); db.commit(); db.refresh(current_user)

    try:
        from services import audit
        audit.log(db, current_user, None, action="2fa_disabled",
                  resource_type="user", resource_id=current_user.id)
    except Exception:
        pass
    return _serialize_user_profile(current_user, tenant)


# ────────────────────────────────────────────────────────────────
# Avatar upload — multipart/form-data
# ────────────────────────────────────────────────────────────────
import os
from fastapi import UploadFile, File

AVATAR_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "uploads", "avatars")
AVATAR_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
AVATAR_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp"}
AVATAR_ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}


@router.post("/me/avatar")
async def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Recibe un archivo y lo guarda como avatar del usuario actual."""
    if file.content_type not in AVATAR_ALLOWED_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de archivo no permitido. Usa PNG, JPG o WebP.")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in AVATAR_ALLOWED_EXT:
        # Inferir por content-type si la extensión es rara.
        ext = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}.get(file.content_type, "png")

    data = await file.read()
    if len(data) > AVATAR_MAX_BYTES:
        raise HTTPException(status_code=400, detail="La imagen pesa más de 2 MB.")
    if not data:
        raise HTTPException(status_code=400, detail="Archivo vacío.")

    os.makedirs(AVATAR_DIR, exist_ok=True)
    fname = f"user-{current_user.id}.{ext}"
    fpath = os.path.join(AVATAR_DIR, fname)
    with open(fpath, "wb") as f:
        f.write(data)

    # URL pública servida por el StaticFiles mount en main.py.
    public_url = f"/static/avatars/{fname}?v={int(datetime.now().timestamp())}"
    current_user.avatar_url = public_url
    current_user.updated_at = datetime.now().isoformat()
    db.add(current_user); db.commit(); db.refresh(current_user)

    try:
        from services import audit
        audit.log(db, current_user, None, action="avatar_update",
                  resource_type="user", resource_id=current_user.id)
    except Exception:
        pass
    return _serialize_user_profile(current_user, tenant)


@router.delete("/me/avatar")
def delete_avatar(
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Elimina el avatar actual (deja el archivo en disco; solo desreferencia)."""
    current_user.avatar_url = None
    current_user.updated_at = datetime.now().isoformat()
    db.add(current_user); db.commit(); db.refresh(current_user)
    return _serialize_user_profile(current_user, tenant)


@router.get("/me/activity")
def my_activity(
    limit: int = 20,
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
    tenant: Tenant = Depends(get_current_tenant),
):
    """Devuelve las últimas N entradas del AuditLog del propio usuario,
    para mostrarlas como "Actividad reciente" en Mi Perfil."""
    from models import AuditLog
    limit = max(1, min(int(limit or 20), 100))
    # user_id ya es único globalmente; tenant_id se respeta cuando está set
    # pero también aceptamos NULL para entradas legacy.
    rows = db.exec(
        select(AuditLog)
        .where(AuditLog.user_id == current_user.id)
        .order_by(AuditLog.id.desc())
        .limit(limit)
    ).all()

    # Labels legibles por acción — fallback al raw action.
    labels = {
        "login":               ("Inicio de sesión", "🔑"),
        "logout":              ("Cierre de sesión", "🚪"),
        "profile_update":      ("Actualización de perfil", "✏️"),
        "password_change":     ("Cambio de contraseña", "🔒"),
        "gdpr_export":         ("Exportación de datos personales", "📦"),
        "gdpr_delete_request": ("Solicitud de eliminación de cuenta", "🗑️"),
        "edit_settings":       ("Cambios en configuración", "⚙️"),
        "session_dispatch":    ("Envío de reunión procesada", "📤"),
    }
    items: list[dict] = []
    for r in rows:
        label, icon = labels.get(r.action, (r.action.replace("_", " ").capitalize(), "•"))
        items.append({
            "id": r.id,
            "action": r.action,
            "label": label,
            "icon": icon,
            "resource_type": r.resource_type,
            "resource_id": r.resource_id,
            "created_at": r.created_at,
            "ip": r.ip,
        })
    return {"items": items, "total": len(items)}


@router.get("/me/permissions")
def read_my_permissions(
    db: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Permisos efectivos del usuario actual.

    Devuelve `{module: [actions]}` agrupado para que el frontend pueda
    consultar `can('reuniones', 'create')` rápido. El admin (`is_superadmin`)
    siempre tiene TODO sin importar lo que diga `RolePermission`.
    """
    from models import RolePermission

    # Super-admin de plataforma → todos los permisos.
    if current_user.is_superadmin:
        full = {k: list(v) for k, v in VALID_MODULES.items()}
        return {
            "role": current_user.role.name if current_user.role else "superadmin",
            "is_superadmin": True,
            "permissions": full,
        }

    role_name = current_user.role.name if current_user.role else None
    if not current_user.role_id:
        return {"role": None, "is_superadmin": False, "permissions": {}}

    # Admin → siempre todos los permisos del catálogo (regla de negocio).
    if role_name and role_name.lower() == "admin":
        full = {k: list(v) for k, v in VALID_MODULES.items()}
        return {"role": role_name, "is_superadmin": False, "permissions": full}

    perms = db.exec(
        select(RolePermission)
        .where(RolePermission.role_id == current_user.role_id)
        .where(RolePermission.is_granted == True)  # noqa: E712
    ).all()
    grouped: dict[str, list[str]] = {}
    for p in perms:
        grouped.setdefault(p.module_key, []).append(p.action)
    return {
        "role": role_name,
        "is_superadmin": False,
        "permissions": grouped,
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
    current_user.updated_at = datetime.now().isoformat()
    db.add(current_user)
    db.commit()

    # Audit trail para que aparezca en "Actividad reciente".
    try:
        from services import audit
        audit.log(db, current_user, None, action="password_change",
                  resource_type="user", resource_id=current_user.id)
    except Exception:  # pragma: no cover
        pass

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
    contra el catálogo y descarta filas inválidas. Hace flush entre el
    delete y el insert para evitar choque con el UNIQUE(role_id, module, action).
    """
    existing = db.exec(
        select(RolePermission).where(RolePermission.role_id == role_id)
    ).all()
    for row in existing:
        db.delete(row)
    # Flush para que el UNIQUE constraint vea los DELETE antes de los INSERT.
    db.flush()

    # Deduplicar el payload (un mismo (mod, act) puede llegar repetido del UI).
    seen: set[tuple[str, str]] = set()
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
        key = (mod, act)
        if key in seen:
            continue
        seen.add(key)
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
    # Borramos permisos y actividad primero (FK sin CASCADE definido).
    for p in db.exec(select(RolePermission).where(RolePermission.role_id == role_id)).all():
        db.delete(p)
    for a in db.exec(select(RoleActivity).where(RoleActivity.role_id == role_id)).all():
        db.delete(a)
    db.flush()
    role_name = role.name
    db.delete(role)
    db.commit()
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

    @field_validator("email")
    @classmethod
    def _norm_email(cls, v: str) -> str:
        return _normalize_email(v)


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
