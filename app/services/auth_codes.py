"""
Lógica compartida para generar y verificar códigos de un solo uso (6 dígitos)
enviados por correo y SMS -- la usan tanto el segundo factor de login del
ADMIN como la recuperación de contraseña (`app/api/v1/auth.py`).

Se centraliza aquí para que las dos rutas se comporten exactamente igual en
lo importante: el código nunca se guarda en texto plano, expira, y tiene un
número limitado de intentos de adivinarlo.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.models.auth_code import AuthCode, AuthCodePurpose
from app.models.user import User
from app.services.email import send_otp_email
from app.services.sms import send_otp_sms


def _generate_numeric_code() -> str:
    """Código de 6 dígitos (000000-999999), generado con el generador
    criptográficamente seguro de Python (no `random`), porque un atacante
    que pudiera predecirlo se saltaría por completo el segundo factor."""
    return f"{secrets.randbelow(1_000_000):06d}"


def issue_code(db: Session, user: User, purpose: AuthCodePurpose) -> None:
    """Genera un código nuevo, invalida cualquier código anterior sin usar del
    mismo propósito (para que solo el último enviado sea válido) y lo envía
    por correo y por SMS (si el usuario tiene teléfono registrado) -- los dos
    canales a la vez, para que si uno falla el otro sirva de respaldo."""
    db.query(AuthCode).filter(
        AuthCode.user_id == user.id,
        AuthCode.purpose == purpose,
        AuthCode.consumed_at.is_(None),
    ).update({"consumed_at": datetime.now(timezone.utc)})

    code = _generate_numeric_code()
    auth_code = AuthCode(
        user_id=user.id,
        purpose=purpose,
        code_hash=hash_password(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.AUTH_CODE_EXPIRE_MINUTES),
    )
    db.add(auth_code)
    db.commit()

    send_otp_email(user.email, code, purpose)
    send_otp_sms(user.phone, code, purpose)


def verify_and_consume_code(db: Session, user: User, purpose: AuthCodePurpose, submitted_code: str) -> bool:
    """Valida un código propuesto contra el más reciente sin usar de ese
    propósito. Si es correcto, lo marca consumido (de un solo uso). Si es
    incorrecto, cuenta el intento -- al agotar los intentos permitidos, ese
    código queda inservible aunque alguien después adivine el valor correcto."""
    auth_code = (
        db.query(AuthCode)
        .filter(
            AuthCode.user_id == user.id,
            AuthCode.purpose == purpose,
            AuthCode.consumed_at.is_(None),
        )
        .order_by(AuthCode.created_at.desc())
        .first()
    )
    if auth_code is None:
        return False

    now = datetime.now(timezone.utc)
    expires_at = auth_code.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < now or auth_code.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS:
        return False

    if not verify_password(submitted_code, auth_code.code_hash):
        auth_code.attempts += 1
        db.commit()
        return False

    auth_code.consumed_at = now
    db.commit()
    return True
