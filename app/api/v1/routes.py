from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import to_shape
from shapely.geometry import Point
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, require_roles
from app.core.database import get_db
from app.models.route import Route, Stop
from app.models.user import Role
from app.schemas.route import RouteCreate, RouteRead, RouteUpdate, StopCreate, StopRead

router = APIRouter(prefix="/routes", tags=["routes"])


def _get_owned_route(db: Session, tenant_id: int, route_id: int) -> Route:
    route = db.query(Route).filter(Route.id == route_id, Route.tenant_id == tenant_id).first()
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ruta no encontrada")
    return route


def _stop_to_read(stop: Stop) -> StopRead:
    point = to_shape(stop.geom)
    return StopRead(
        id=stop.id,
        route_id=stop.route_id,
        name=stop.name,
        order_index=stop.order_index,
        geofence_radius_m=stop.geofence_radius_m,
        lon=point.x,
        lat=point.y,
    )


@router.get("", response_model=list[RouteRead])
def list_routes(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> list[Route]:
    return db.query(Route).filter(Route.tenant_id == tenant_id).all()


@router.post("", response_model=RouteRead, status_code=status.HTTP_201_CREATED)
def create_route(
    payload: RouteCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> Route:
    route = Route(tenant_id=tenant_id, **payload.model_dump())
    db.add(route)
    db.commit()
    db.refresh(route)
    return route


@router.put("/{route_id}", response_model=RouteRead)
def update_route(
    route_id: int,
    payload: RouteUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> Route:
    route = _get_owned_route(db, tenant_id, route_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(route, field, value)
    db.commit()
    db.refresh(route)
    return route


@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_route(
    route_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> None:
    route = _get_owned_route(db, tenant_id, route_id)
    db.delete(route)
    db.commit()


@router.get("/{route_id}/stops", response_model=list[StopRead])
def list_stops(
    route_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> list[StopRead]:
    _get_owned_route(db, tenant_id, route_id)  # valida pertenencia al tenant
    stops = (
        db.query(Stop)
        .filter(Stop.route_id == route_id, Stop.tenant_id == tenant_id)
        .order_by(Stop.order_index)
        .all()
    )
    return [_stop_to_read(s) for s in stops]


@router.post("/{route_id}/stops", response_model=StopRead, status_code=status.HTTP_201_CREATED)
def create_stop(
    route_id: int,
    payload: StopCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> StopRead:
    _get_owned_route(db, tenant_id, route_id)
    point_wkt = f"SRID=4326;POINT({payload.location.lon} {payload.location.lat})"
    stop = Stop(
        tenant_id=tenant_id,
        route_id=route_id,
        name=payload.name,
        order_index=payload.order_index,
        geofence_radius_m=payload.geofence_radius_m,
        geom=point_wkt,
    )
    db.add(stop)
    db.commit()
    db.refresh(stop)
    return _stop_to_read(stop)
