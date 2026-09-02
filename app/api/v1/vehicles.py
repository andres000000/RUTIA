from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, require_roles
from app.core.database import get_db
from app.models.user import Role
from app.models.vehicle import Vehicle
from app.schemas.vehicle import VehicleCreate, VehicleRead, VehicleUpdate

router = APIRouter(prefix="/vehicles", tags=["vehicles"])


@router.get("", response_model=list[VehicleRead])
def list_vehicles(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> list[Vehicle]:
    return db.query(Vehicle).filter(Vehicle.tenant_id == tenant_id).all()


@router.post("", response_model=VehicleRead, status_code=status.HTTP_201_CREATED)
def create_vehicle(
    payload: VehicleCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> Vehicle:
    vehicle = Vehicle(tenant_id=tenant_id, **payload.model_dump())
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


def _get_owned_vehicle(db: Session, tenant_id: int, vehicle_id: int) -> Vehicle:
    vehicle = (
        db.query(Vehicle).filter(Vehicle.id == vehicle_id, Vehicle.tenant_id == tenant_id).first()
    )
    if vehicle is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bus no encontrado")
    return vehicle


@router.put("/{vehicle_id}", response_model=VehicleRead)
def update_vehicle(
    vehicle_id: int,
    payload: VehicleUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> Vehicle:
    vehicle = _get_owned_vehicle(db, tenant_id, vehicle_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vehicle, field, value)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vehicle(
    vehicle_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> None:
    vehicle = _get_owned_vehicle(db, tenant_id, vehicle_id)
    db.delete(vehicle)
    db.commit()
