"""
Autenticación y seguridad de cuentas.

Diseño de seguridad (resumen para la sustentación):

1. **Contraseñas:** nunca se guardan en texto plano, se hashean con bcrypt
   (`app/core/security.py`).
2. **Bloqueo por fuerza bruta:** tras `LOGIN_MAX_FAILED_ATTEMPTS` contraseñas
   incorrectas seguidas, la cuenta se bloquea `LOGIN_LOCKOUT_MINUTES` minutos,
   sin importar que después se escriba la contraseña correcta.
3. **Segundo factor (2FA) por correo, solo para ADMIN:** quien administra un
   colegio entero es la cuenta más sensible de la plataforma. Su login no
   entrega el token de una vez: primero valida la contraseña, luego envía un
   código de 6 dígitos al correo registrado, y solo con ese código
   (POST /auth/verify-login) se entrega el token real. Los roles de la app
   móvil (PADRE/CONDUCTOR/MONITOR) no pasan por esto -- necesitan poder
   entrar rápido desde el celular en la ruta, y ya están protegidos por el
   mismo bloqueo por fuerza bruta.
4. **Recuperación de contraseña sin exponer si un correo existe:**
   POST /auth/forgot-password siempre responde igual, exista o no esa cuenta,
   para no dejarle saber a un atacante qué correos están registrados.
5. **Códigos de un solo uso:** cada código (2FA o recuperación) se guarda
   hasheado, expira a los pocos minutos y tiene un límite de intentos de
   adivinarlo (`app/services/auth_codes.py`).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_user_access_token, hash_password, verify_password
from app.models.auth_code import AuthCodePurpose
from app.models.user import Role, User
from app.schemas.auth import (
    CurrentUser,
    ForgotPasswordRequest,
    LoginRequest,
    LoginResult,
    MessageResponse,
    ResetPasswordRequest,
    TokenResponse,
    VerifyLoginRequest,
)
from app.services.auth_codes import issue_code, verify_and_consume_code

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Correo o contraseña incorrectos",
)


def _issue_token_for(user: User) -> str:
    return create_user_access_token(user_id=user.id, tenant_id=user.tenant_id, role=user.role.value)


def _lockout_message(user: User) -> str:
    remaining = user.locked_until - datetime.now(timezone.utc)
    minutes = max(int(remaining.total_seconds() // 60) + 1, 1)
    return f"Cuenta bloqueada temporalmente por varios intentos fallidos. Intenta de nuevo en {minutes} minuto(s)."


@router.post("/login", response_model=LoginResult)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResult:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        raise _INVALID_CREDENTIALS

    now = datetime.now(timezone.utc)
    locked_until = user.locked_until
    if locked_until is not None:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=timezone.utc)
        if locked_until > now:
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=_lockout_message(user))

    if not verify_password(payload.password, user.hashed_password):
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
            db.commit()
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=_lockout_message(user))
        db.commit()
        raise _INVALID_CREDENTIALS

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")

    # Contraseña correcta: limpiar el contador de intentos fallidos.
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    if user.role == Role.ADMIN:
        issue_code(db, user, AuthCodePurpose.LOGIN_2FA)
        return LoginResult(access_token=None, requires_verification=True, email=user.email)

    return LoginResult(access_token=_issue_token_for(user), requires_verification=False)


@router.post("/verify-login", response_model=TokenResponse)
def verify_login(payload: VerifyLoginRequest, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido o vencido")

    if not verify_and_consume_code(db, user, AuthCodePurpose.LOGIN_2FA, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido o vencido")

    return TokenResponse(access_token=_issue_token_for(user))


@router.post("/forgot-password", response_model=MessageResponse)
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)) -> MessageResponse:
    generic_message = MessageResponse(
        message="Si ese correo está registrado, te enviamos un código de recuperación."
    )
    user = db.query(User).filter(User.email == payload.email).first()
    if user is not None and user.is_active:
        issue_code(db, user, AuthCodePurpose.PASSWORD_RESET)
    # Misma respuesta exista o no la cuenta: así nadie puede usar este
    # endpoint para averiguar qué correos están registrados en la plataforma.
    return generic_message


@router.post("/reset-password", response_model=MessageResponse)
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)) -> MessageResponse:
    invalid_code_error = HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido o vencido"
    )
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        raise invalid_code_error

    if not verify_and_consume_code(db, user, AuthCodePurpose.PASSWORD_RESET, payload.code):
        raise invalid_code_error

    user.hashed_password = hash_password(payload.new_password)
    # Un restablecimiento exitoso demuestra que quien lo hizo es el dueño de
    # la cuenta (tuvo acceso al correo), así que de paso se levanta cualquier
    # bloqueo por intentos fallidos anterior.
    user.failed_login_attempts = 0
    user.locked_until = None
    db.commit()

    return MessageResponse(message="Contraseña actualizada correctamente. Ya puedes iniciar sesión.")


@router.get("/me", response_model=CurrentUser)
def read_me(current_user: User = Depends(get_current_user)) -> CurrentUser:
    return CurrentUser.model_validate(current_user)
