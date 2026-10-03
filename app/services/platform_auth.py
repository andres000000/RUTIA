"""
Segundo factor (2FA) y bloqueo por intentos del operador de plataforma.

El operador no es un usuario (no tiene fila en `users`), así que no puede
usar la tabla `auth_codes` ni el contador de intentos de `users` como el
ADMIN de un colegio. El estado se guarda en memoria del proceso:

- Railway corre un solo proceso de uvicorn (ver el CMD del Dockerfile), así
  que todas las peticiones ven el mismo estado.
- Si el servidor se reinicia a mitad de un login, el código pendiente se
  pierde y basta con volver a ingresar la clave: no hay nada que deba
  sobrevivir un reinicio (no se crean tablas ni migraciones para esto).

Mismas reglas que el ADMIN de un colegio, o más estrictas:
- La clave se compara en tiempo constante (`secrets.compare_digest`).
- 5 claves incorrectas seguidas bloquean el acceso de operador 15 minutos.
- El código es de 6 dígitos, se guarda solo su hash, vence a los 10 min y se
  invalida tras 5 intentos fallidos. Solo el último código enviado sirve.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.services.email import send_platform_otp_email


class PlatformLocked(Exception):
    def __init__(self, minutes: int):
        self.minutes = minutes


class InvalidPlatformKey(Exception):
    pass


@dataclass
class _Challenge:
    id: str
    code_hash: str
    expires_at: datetime
    attempts: int = 0


@dataclass
class _State:
    failed_keys: int = 0
    locked_until: datetime | None = None
    challenge: _Challenge | None = None


_state = _State()


def reset_state() -> None:
    """Para las pruebas automatizadas: cada prueba arranca sin bloqueos ni
    códigos pendientes de otra."""
    global _state
    _state = _State()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def mask_email(email: str) -> str:
    """"operador@gmail.com" -> "op•••••@gmail.com", para decirle al operador a
    dónde se envió el código sin exponer el correo completo en la pantalla de
    login (que ve cualquiera que tenga la clave)."""
    local, _, domain = email.partition("@")
    if not domain:
        return ""
    return f"{local[:2]}{'•' * max(len(local) - 2, 3)}@{domain}"


def start_login(platform_key: str) -> str:
    """Valida la clave y envía un código nuevo. Devuelve el id del desafío,
    que el frontend devuelve junto con el código en el segundo paso."""
    now = _now()
    if _state.locked_until is not None and _state.locked_until > now:
        remaining = _state.locked_until - now
        raise PlatformLocked(max(int(remaining.total_seconds() // 60) + 1, 1))

    if not secrets.compare_digest(platform_key.encode(), settings.PLATFORM_BOOTSTRAP_KEY.encode()):
        _state.failed_keys += 1
        if _state.failed_keys >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            _state.failed_keys = 0
            _state.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            raise PlatformLocked(settings.LOGIN_LOCKOUT_MINUTES)
        raise InvalidPlatformKey()

    _state.failed_keys = 0
    _state.locked_until = None
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = _Challenge(
        id=secrets.token_urlsafe(24),
        code_hash=hash_password(code),
        expires_at=now + timedelta(minutes=settings.AUTH_CODE_EXPIRE_MINUTES),
    )
    _state.challenge = challenge  # reemplaza cualquier código anterior sin usar
    send_platform_otp_email(settings.PLATFORM_OPERATOR_EMAIL, code)
    return challenge.id


def verify_code(challenge_id: str, code: str) -> bool:
    challenge = _state.challenge
    if challenge is None or not secrets.compare_digest(challenge.id.encode(), challenge_id.encode()):
        return False
    if challenge.expires_at < _now() or challenge.attempts >= settings.AUTH_CODE_MAX_ATTEMPTS:
        _state.challenge = None
        return False
    if not verify_password(code, challenge.code_hash):
        challenge.attempts += 1
        return False
    _state.challenge = None  # de un solo uso
    return True
