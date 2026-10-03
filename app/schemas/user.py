from pydantic import BaseModel, EmailStr, Field

from app.models.user import Role
from app.schemas.common import NormalizedEmail


class UserCreate(BaseModel):
    email: NormalizedEmail
    password: str = Field(..., min_length=8)
    full_name: str
    phone: str | None = None
    role: Role


class UserUpdate(BaseModel):
    full_name: str | None = None
    phone: str | None = None
    is_active: bool | None = None
    # Para corregir un correo mal escrito o asignar una contraseña temporal
    # nueva cuando la persona la olvidó (sin pasar por el correo).
    email: NormalizedEmail | None = None
    password: str | None = Field(None, min_length=8)


class UserRead(BaseModel):
    id: int
    tenant_id: int
    email: EmailStr
    full_name: str
    phone: str | None = None
    role: Role
    is_active: bool

    model_config = {"from_attributes": True}
