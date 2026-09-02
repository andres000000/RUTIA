from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import settings
from app.core.database import get_db
from app.models.tenant import Tenant
from app.models.user import Role, User
from app.schemas.tenant import (
    TenantBootstrapRequest,
    TenantBootstrapResponse,
    TenantRead,
)
from app.services.tenants import TenantEmailAlreadyRegistered, create_tenant_with_admin

router = APIRouter(prefix="/tenants", tags=["tenants"])


@router.post("/bootstrap", response_model=TenantBootstrapResponse, status_code=status.HTTP_201_CREATED)
def bootstrap_tenant(
    payload: TenantBootstrapRequest,
    db: Session = Depends(get_db),
    x_bootstrap_key: str = Header(..., alias="X-Bootstrap-Key"),
) -> TenantBootstrapResponse:
    """
    Da de alta un nuevo colegio (tenant) junto con su primer usuario ADMIN.
    Protegido por una clave de plataforma (no por un rol, porque ningún usuario
    todavía pertenece al tenant que se está creando). Ver también
    `POST /platform/tenants`, que hace lo mismo pero autenticado con la sesión
    de operador de plataforma en vez de este header -- pensado para crearse
    desde el panel de selección de colegios en vez de con curl/Postman.
    """
    if x_bootstrap_key != settings.PLATFORM_BOOTSTRAP_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Clave de plataforma inválida")

    try:
        tenant = create_tenant_with_admin(db, payload)
    except TenantEmailAlreadyRegistered:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El correo ya está registrado")

    return TenantBootstrapResponse(tenant=TenantRead.model_validate(tenant), admin_email=payload.admin_email)


@router.get("/me", response_model=TenantRead)
def read_my_tenant(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles(Role.ADMIN)),
) -> TenantRead:
    tenant = db.get(Tenant, current_user.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Colegio no encontrado")
    return TenantRead.model_validate(tenant)
