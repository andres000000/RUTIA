from pydantic import BaseModel, EmailStr, Field

from app.schemas.tenant import TenantRead


class PlatformLoginRequest(BaseModel):
    """El operador de plataforma no tiene usuario/contraseña propios: entra con
    la misma clave compartida que ya protegía `POST /tenants/bootstrap`
    (`PLATFORM_BOOTSTRAP_KEY`)."""

    platform_key: str = Field(..., min_length=1)


class PlatformLoginChallenge(BaseModel):
    """Primer paso del login de operador: la clave fue correcta y se envió un
    código. `sent_to` es el correo enmascarado (o None si no hay correo de
    operador configurado y el código solo quedó en el log del servidor)."""

    requires_verification: bool = True
    challenge_id: str
    sent_to: str | None = None


class PlatformVerifyRequest(BaseModel):
    challenge_id: str = Field(..., min_length=1)
    code: str = Field(..., min_length=6, max_length=6)


class PlatformTenantSummary(TenantRead):
    """Un colegio visto desde el panel del operador de plataforma, con
    algunas cifras rápidas para que la tarjeta de cada colegio diga algo útil
    sin tener que entrar a administrarlo primero."""

    admin_email: EmailStr | None = None
    vehicles_count: int = 0
    routes_count: int = 0
    students_count: int = 0


class PlatformEnterTenantResponse(BaseModel):
    """Respuesta de `POST /platform/tenants/{id}/enter`: un token de sesión
    normal de ADMIN para ese colegio (igual al que entrega
    `POST /auth/verify-login`), listo para usarse en cualquier endpoint
    existente sin ningún cambio."""

    access_token: str
    token_type: str = "bearer"
    tenant: TenantRead
