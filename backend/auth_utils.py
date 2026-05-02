import logging
import os
from datetime import datetime, timedelta
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from pydantic import BaseModel

from config import settings

logger = logging.getLogger(__name__)


def _resolve_secret_key() -> str:
    """Load JWT secret from settings/env. Fail loud in production if missing or weak."""
    key = settings.jwt_secret_key or os.getenv("JWT_SECRET_KEY", "")
    if not key:
        if os.getenv("ENVIRONMENT", "").lower() in ("prod", "production"):
            raise RuntimeError(
                "JWT_SECRET_KEY no está configurada. Define la variable de entorno "
                "antes de arrancar el servidor en producción."
            )
        logger.warning(
            "JWT_SECRET_KEY no está configurada. Usando valor temporal solo para desarrollo. "
            "Define JWT_SECRET_KEY en .env para emitir tokens válidos."
        )
        # Local development fallback. Tokens emitted with this key will not survive a restart.
        key = os.urandom(64).hex()
    if len(key) < 32:
        raise RuntimeError("JWT_SECRET_KEY debe tener al menos 32 caracteres.")
    return key


SECRET_KEY = _resolve_secret_key()
ALGORITHM = settings.jwt_algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes
BCRYPT_ROUNDS = max(settings.bcrypt_rounds, 12)


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[str] = None
    role: Optional[str] = None


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def get_password_hash(password: str) -> str:
    salt = bcrypt.gensalt(rounds=BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_password_reset_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode = {"exp": expire, "sub": email, "type": "reset"}
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def verify_password_reset_token(token: str) -> Optional[str]:
    try:
        decoded_token = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if decoded_token.get("type") != "reset":
            return None
        return decoded_token.get("sub")
    except JWTError:
        return None
