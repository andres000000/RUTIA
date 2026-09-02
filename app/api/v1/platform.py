"""
Panel del operador de plataforma ("súper-admin"): quien tiene la clave de
plataforma (`PLATFORM_BOOTSTRAP_KEY`) puede ver todos los colegios
registrados en RUTIA, dar de alta colegios nuevos desde el panel web (sin
tener que usar curl/Postman) y entrar a administrar cualquiera de ellos.

Esto es un rol *aparte* de ADMIN: no es un usuario que pertenezca a un
colegio (no hay fila en `users` para el operador de plataforma), es quien
opera la plataforma completa -- pensado para este proyecto académico donde
una sola persona hace ambos papeles, pero sin mezclar los dos conceptos:
cada colegio sigue teniendo su propio ADMIN aislado del resto (con su propio
2FA, su propio bloqueo por intentos, etc.), exactamente como ya estaba
probado. "Entrar" a un colegio (`/platform/tenants/{id}/enter`) no reemplaza
ni evita esas protecciones del lado del colegio -- lo que hace es iniciar una
sesión de ADMIN de ese colegio en nombre del operador de plataforma, quien ya
demostró tener la clave más sensible de todas (la que puede crear colegios
enteros). Por eso esa sesión no vuelve a pedir el código de 2FA del ADMIN de
ese colegio en particular.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_super_admin
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, create_user_access_token
from app.models.route import Route
from app.models.student import Student
from app.models.tenant import Tenant
from app.models.user import Role, User
from app.models.vehicle import Vehicle
from app.schemas.platform import (
    PlatformEnterTenantResponse,
    PlatformLoginRequest,
    PlatformTenantSummary,
)
from app.schemas.tenant import TenantBootstrapRequest, TenantBootstrapResponse, TenantRead
from app.schemas.auth import TokenResponse
from app.services.tenants import TenantEmailAlreadyRegistered, create_tenant_with_admin

router = APIRouter(prefix="/platform", tags=["platform"])


@router.post("/login", response_model=TokenResponse)
def platform_login(payload: PlatformLoginRequest) -> TokenResponse:
    if payload.platform_key != settings.PLATFORM_BOOTSTRAP_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Clave de plataforma inválida")

    token = create_access_token(subject="platform", extra_claims={"scope": "SUPER_ADMIN"})
    return TokenResponse(access_token=token)


@router.get("/tenants", response_model=list[PlatformTenantSummary])
def list_all_tenants(
    db: Session = Depends(get_db),
    _platform=Depends(require_super_admin),
) -> list[PlatformTenantSummary]:
    tenants = db.query(Tenant).order_by(Tenant.name).all()
    summaries: list[PlatformTenantSummary] = []
    for tenant in tenants:
        admin_user = (
            db.query(User)
            .filter(User.tenant_id == tenant.id, User.role == Role.ADMIN)
            .order_by(User.id)
            .first()
        )
        summaries.append(
            PlatformTenantSummary(
                id=tenant.id,
                name=tenant.name,
                nit=tenant.nit,
                address=tenant.address,
                phone=tenant.phone,
                is_active=tenant.is_active,
                admin_email=admin_user.email if admin_user else None,
                vehicles_count=db.query(Vehicle).filter(Vehicle.tenant_id == tenant.id).count(),
                routes_count=db.query(Route).filter(Route.tenant_id == tenant.id).count(),
                students_count=db.query(Student).filter(Student.tenant_id == tenant.id).count(),
            )
        )
    return summaries


@router.post("/tenants", response_model=TenantBootstrapResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantBootstrapRequest,
    db: Session = Depends(get_db),
    _platform=Depends(require_super_admin),
) -> TenantBootstrapResponse:
    """Igual que `POST /tenants/bootstrap`, pero autenticado con la sesión de
    operador de plataforma en vez del header `X-Bootstrap-Key` -- lo usa el
    botón "Agregar colegio" del panel de selección de colegios."""
    try:
        tenant = create_tenant_with_admin(db, payload)
    except TenantEmailAlreadyRegistered:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="El correo ya está registrado")

    return TenantBootstrapResponse(tenant=TenantRead.model_validate(tenant), admin_email=payload.admin_email)


@router.post("/tenants/{tenant_id}/enter", response_model=PlatformEnterTenantResponse)
def enter_tenant(
    tenant_id: int,
    db: Session = Depends(get_db),
    _platform=Depends(require_super_admin),
) -> PlatformEnterTenantResponse:
    tenant = db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Colegio no encontrado")

    admin_user = (
        db.query(User)
        .filter(User.tenant_id == tenant.id, User.role == Role.ADMIN)
        .order_by(User.id)
        .first()
    )
    if admin_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Este colegio todavía no tiene ningún usuario ADMIN",
        )

    token = create_user_access_token(
        user_id=admin_user.id, tenant_id=admin_user.tenant_id, role=admin_user.role.value
    )
    return PlatformEnterTenantResponse(access_token=token, tenant=TenantRead.model_validate(tenant))
