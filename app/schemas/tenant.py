from pydantic import BaseModel, EmailStr, Field


class TenantRead(BaseModel):
    id: int
    name: str
    nit: str | None = None
    address: str | None = None
    phone: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}


class TenantBootstrapRequest(BaseModel):
    """Payload para dar de alta un nuevo colegio junto con su primer usuario ADMIN."""

    name: str = Field(..., min_length=2, max_length=150)
    nit: str | None = None
    address: str | None = None
    phone: str | None = None
    admin_email: EmailStr
    admin_password: str = Field(..., min_length=8)
    admin_full_name: str


class TenantBootstrapResponse(BaseModel):
    tenant: TenantRead
    admin_email: EmailStr
