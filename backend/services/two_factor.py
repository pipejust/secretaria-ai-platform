"""Autenticación en dos pasos por email-OTP.

Flujo:
- `start_challenge(user, purpose)` → genera un código de 6 dígitos, lo hashea
   y lo persiste en `User.two_factor_code_*`, y envía un correo con el código.
- `verify_code(user, code)` → compara el hash, valida expiración y máx de
   intentos (5). Si OK, limpia las columnas y devuelve True.
- `clear(user)` → resetea el challenge (después de éxito o cancelación).

Los códigos viven 10 minutos. La verificación NO levanta excepción si el
código es inválido — devuelve False y deja al endpoint mapear el error a
HTTP 400. Esto permite reusar el helper desde múltiples sitios sin
acoplarlo a FastAPI.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session

from models import User

logger = logging.getLogger(__name__)

CODE_TTL_MINUTES = 10
MAX_ATTEMPTS = 5
CODE_LENGTH = 6


def _hash_code(code: str) -> str:
    """SHA-256 del código + sal estática. El código nunca se persiste en claro."""
    salted = ("acten-2fa:" + code).encode("utf-8")
    return hashlib.sha256(salted).hexdigest()


def _generate_code() -> str:
    """Código numérico de 6 dígitos. `secrets` evita números predecibles."""
    return "".join(str(secrets.randbelow(10)) for _ in range(CODE_LENGTH))


def start_challenge(db: Session, user: User, *, purpose: str) -> str:
    """Genera + persiste + envía un nuevo código. Devuelve el código en claro
    al caller para que en modo dev (sin SMTP) lo pueda mostrar en logs.

    `purpose` ∈ {'enable', 'login'}:
    - 'enable': el usuario está activando 2FA desde su perfil.
    - 'login': el usuario ya tiene 2FA activo y está logueándose.
    """
    code = _generate_code()
    user.two_factor_code_hash = _hash_code(code)
    user.two_factor_code_expires_at = (datetime.now() + timedelta(minutes=CODE_TTL_MINUTES)).isoformat()
    user.two_factor_code_purpose = purpose
    user.two_factor_attempts = 0
    db.add(user)
    db.commit()
    db.refresh(user)

    # Disparar email vía la plantilla branded (logo + colores del tenant).
    try:
        import asyncio
        from services.email_service import EmailService

        svc = EmailService(db=db, tenant_id=user.tenant_id)

        async def _dispatch() -> None:
            await svc.send_two_factor_code_email(
                to_email=user.email,
                user_name=user.full_name or user.email,
                code=code,
                purpose=purpose,
                ttl_minutes=CODE_TTL_MINUTES,
                max_attempts=MAX_ATTEMPTS,
            )

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Estamos dentro de un endpoint async — agenda como background task.
                asyncio.ensure_future(_dispatch())
            else:
                loop.run_until_complete(_dispatch())
        except RuntimeError:
            asyncio.run(_dispatch())
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo enviar el correo de 2FA — el código sigue válido en BD.")

    return code


def verify_code(db: Session, user: User, code: str, *, purpose: Optional[str] = None) -> tuple[bool, str]:
    """Verifica el código contra `user.two_factor_code_hash`. Devuelve
    `(ok, reason)`. `reason` es legible para el operador en caso de fallo."""
    if not user.two_factor_code_hash or not user.two_factor_code_expires_at:
        return False, "No hay un código activo. Solicita uno nuevo."
    if purpose and user.two_factor_code_purpose != purpose:
        return False, "El código solicitado no coincide con la operación."
    try:
        expires = datetime.fromisoformat(user.two_factor_code_expires_at)
    except Exception:
        return False, "Código corrupto. Solicita uno nuevo."
    if datetime.now() > expires:
        clear(db, user)
        return False, "El código ha expirado. Solicita uno nuevo."
    if user.two_factor_attempts >= MAX_ATTEMPTS:
        clear(db, user)
        return False, "Demasiados intentos. Solicita un código nuevo."
    if _hash_code(code.strip()) != user.two_factor_code_hash:
        user.two_factor_attempts = (user.two_factor_attempts or 0) + 1
        db.add(user); db.commit()
        return False, "Código incorrecto."
    return True, "OK"


def clear(db: Session, user: User) -> None:
    """Resetea los campos transitorios del challenge."""
    user.two_factor_code_hash = None
    user.two_factor_code_expires_at = None
    user.two_factor_code_purpose = None
    user.two_factor_attempts = 0
    db.add(user); db.commit()
