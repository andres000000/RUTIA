"""
Lógica compartida para dar de alta un colegio (tenant) junto con su primer
usuario ADMIN. La usan dos caminos de entrada distintos:

1. `POST /tenants/bootstrap` -- protegido con la clave de plataforma en un
   header, pensado para darle de alta el primer colegio a alguien externo
   sin sesión previa.
2. `POST /platform/tenants` -- protegido con el token de operador de
   plataforma (súper-admin), pensado para crear colegios nuevos desde el
   panel de selección de colegios, sin necesidad de usar curl/Postman.

Centralizar esto en un solo lugar evita que las dos rutas se comporten
distinto por accidente.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import Role, User
from app.schemas.tenant import TenantBootstrapRequest


class TenantEmailAlreadyRegistered(Exception):
    """El correo del administrador que se quiere crear ya existe en la plataforma."""


def create_tenant_with_admin(db: Session, payload: TenantBootstrapRequest) -> Tenant:
    existing = db.query(User).filter(User.email == payload.admin_email).first()
    if existing is not None:
        raise TenantEmailAlreadyRegistered(payload.admin_email)

    tenant = Tenant(name=payload.name, nit=payload.nit, address=payload.address, phone=payload.phone)
    db.add(tenant)
    db.flush()  # asigna tenant.id sin cerrar la transacción

    admin_user = User(
        tenant_id=tenant.id,
        email=payload.admin_email,
        hashed_password=hash_password(payload.admin_password),
        full_name=payload.admin_full_name,
        role=Role.ADMIN,
    )
    db.add(admin_user)
    db.commit()
    db.refresh(tenant)
    return tenant
