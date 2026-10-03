from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from app.core.config import settings

# Se usa la librería `bcrypt` directamente (en vez de passlib) porque las versiones
# recientes de bcrypt rompieron la detección de versión interna de passlib 1.7.4
# (proyecto sin mantenimiento activo), lo que provocaba errores intermitentes al
# hashear contraseñas. bcrypt a secas es la dependencia más simple y estable aquí.
_BCRYPT_MAX_BYTES = 72  # límite duro del algoritmo bcrypt


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(password_bytes, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    password_bytes = plain_password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.checkpw(password_bytes, hashed_password.encode("utf-8"))


def create_access_token(
    subject: str, extra_claims: dict[str, Any] | None = None, expire_minutes: int | None = None
) -> str:
    minutes = expire_minutes if expire_minutes is not None else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    to_encode: dict[str, Any] = {"sub": subject, "exp": expire}
    if extra_claims:
        to_encode.update(extra_claims)
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any] | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None


def create_user_access_token(user_id: int, tenant_id: int, role: str) -> str:
    """
    Token de sesión normal de un usuario de un colegio (cualquier rol). Único
    lugar que arma este tipo de token, para que dos caminos distintos nunca
    generen tokens con forma distinta:
    - El login normal (`app/api/v1/auth.py`, tras la contraseña -- y, si es
      ADMIN, tras el código de 2FA).
    - El operador de plataforma "entrando" a administrar un colegio sin pasar
      por el login de ese colegio (`app/api/v1/platform.py`).
    """
    return create_access_token(subject=str(user_id), extra_claims={"tenant_id": tenant_id, "role": role})
