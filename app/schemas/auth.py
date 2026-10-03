from pydantic import BaseModel, EmailStr, Field

from app.models.user import Role
from app.schemas.common import NormalizedEmail


class LoginRequest(BaseModel):
    email: NormalizedEmail
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginResult(BaseModel):
    """Respuesta de POST /auth/login. Para los roles de la app móvil
    (PADRE/CONDUCTOR/MONITOR) se comporta exactamente como antes: llega el
    `access_token` de una vez. Para ADMIN, en cambio, `access_token` viene
    vacío y `requires_verification=True` -- el frontend debe pedirle el
    código de 6 dígitos que se le envió por correo y llamar a
    POST /auth/verify-login para obtener el token real."""

    access_token: str | None = None
    token_type: str = "bearer"
    requires_verification: bool = False
    email: EmailStr | None = None


class VerifyLoginRequest(BaseModel):
    email: NormalizedEmail
    code: str = Field(..., min_length=6, max_length=6)


class ForgotPasswordRequest(BaseModel):
    email: NormalizedEmail


class ResetPasswordRequest(BaseModel):
    email: NormalizedEmail
    code: str = Field(..., min_length=6, max_length=6)
    new_password: str = Field(..., min_length=8)


class MessageResponse(BaseModel):
    message: str


class CurrentUser(BaseModel):
    id: int
    tenant_id: int
    email: EmailStr
    full_name: str
    role: Role

    model_config = {"from_attributes": True}
