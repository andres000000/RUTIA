from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.elements import WKTElement
from geoalchemy2.shape import to_shape
from shapely.geometry import Point
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, require_roles
from app.core.database import get_db
from app.models.route import Route, Stop
from app.models.user import Role
from app.schemas.route import (
    PointIn,
    RouteCreate,
    RouteOptimizationRead,
    RoutePathRead,
    RouteRead,
    RouteUpdate,
    StopCreate,
    StopOrderUpdate,
    StopRead,
    StopUpdate,
)
from app.services.routing import fetch_road_path, polyline_length_m, propose_stop_order

router = APIRouter(prefix="/routes", tags=["routes"])

# Ahorro mínimo para que valga la pena sugerir cambiar el orden: por debajo de
# esto, la "mejora" es ruido del cálculo y solo confundiría al admin.
MIN_SAVING_M = 20.0


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
        student_id=stop.student_id,
    )


def _ordered_stops(db: Session, tenant_id: int, route_id: int) -> list[Stop]:
    return (
        db.query(Stop)
        .filter(Stop.route_id == route_id, Stop.tenant_id == tenant_id)
        .order_by(Stop.order_index, Stop.id)
        .all()
    )


def _get_owned_stop(db: Session, tenant_id: int, route_id: int, stop_id: int) -> Stop:
    stop = (
        db.query(Stop)
        .filter(Stop.id == stop_id, Stop.route_id == route_id, Stop.tenant_id == tenant_id)
        .first()
    )
    if stop is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Parada no encontrada")
    return stop


def _point(location: PointIn) -> WKTElement:
    # WKTElement (y no un string "SRID=4326;POINT(...)") para que `to_shape`
    # funcione también sobre una parada recién creada que aún no se ha
    # recargado desde la base de datos.
    return WKTElement(f"POINT({location.lon} {location.lat})", srid=4326)


def _stop_coords(stops: list[Stop]) -> list[tuple[float, float]]:
    return [(p.x, p.y) for p in (to_shape(s.geom) for s in stops)]


def _drawn_length_m(coords: list[tuple[float, float]]) -> float:
    """Largo del trazado por calles medido sobre la línea dibujada, igual que
    lo calcula `GET .../path`: así el "ahorro" que ve el admin al optimizar
    coincide con la distancia que muestra el mapa después de aplicarlo."""
    if len(coords) < 2:
        return 0.0
    return polyline_length_m(fetch_road_path(coords).coordinates)


def _refresh_route_path(db: Session, route: Route) -> None:
    """Recalcula el trazado por calles después de cualquier cambio en las paradas.

    Solo se guarda en `Route.geom` un trazado de OSRM: si OSRM no respondió, se
    deja en NULL para que `GET .../path` lo vuelva a intentar la próxima vez,
    en vez de quedarse para siempre con líneas rectas. Las paradas también se
    renumeran 1..n para que el orden quede compacto tras borrar o mover."""
    stops = _ordered_stops(db, route.tenant_id, route.id)
    for index, stop in enumerate(stops, start=1):
        stop.order_index = index
    route.geom = None
    if len(stops) >= 2:
        path = fetch_road_path(_stop_coords(stops))
        if path.source == "osrm":
            points = ", ".join(f"{lon} {lat}" for lon, lat in path.coordinates)
            route.geom = WKTElement(f"LINESTRING({points})", srid=4326)


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
    return [_stop_to_read(s) for s in _ordered_stops(db, tenant_id, route_id)]


@router.post("/{route_id}/stops", response_model=StopRead, status_code=status.HTTP_201_CREATED)
def create_stop(
    route_id: int,
    payload: StopCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> StopRead:
    route = _get_owned_route(db, tenant_id, route_id)
    point_wkt = _point(payload.location)
    stop = Stop(
        tenant_id=tenant_id,
        route_id=route_id,
        name=payload.name,
        order_index=payload.order_index,
        geofence_radius_m=payload.geofence_radius_m,
        geom=point_wkt,
    )
    db.add(stop)
    db.flush()
    _refresh_route_path(db, route)
    db.commit()
    db.refresh(stop)
    return _stop_to_read(stop)


@router.put("/{route_id}/stops/order", response_model=list[StopRead])
def reorder_stops(
    route_id: int,
    payload: StopOrderUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> list[StopRead]:
    route = _get_owned_route(db, tenant_id, route_id)
    stops = {s.id: s for s in _ordered_stops(db, tenant_id, route_id)}
    # Se exige la lista completa y sin repetidos: un orden parcial dejaría
    # paradas con posiciones ambiguas.
    if sorted(payload.stop_ids) != sorted(stops):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="La lista debe contener exactamente todas las paradas de la ruta, sin repetir.",
        )
    for index, stop_id in enumerate(payload.stop_ids, start=1):
        stops[stop_id].order_index = index
    db.flush()
    _refresh_route_path(db, route)
    db.commit()
    return [_stop_to_read(s) for s in _ordered_stops(db, tenant_id, route_id)]


@router.put("/{route_id}/stops/{stop_id}", response_model=StopRead)
def update_stop(
    route_id: int,
    stop_id: int,
    payload: StopUpdate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> StopRead:
    route = _get_owned_route(db, tenant_id, route_id)
    stop = _get_owned_stop(db, tenant_id, route_id, stop_id)
    if payload.name is not None:
        stop.name = payload.name
    if payload.geofence_radius_m is not None:
        stop.geofence_radius_m = payload.geofence_radius_m
    if payload.location is not None:
        stop.geom = _point(payload.location)
        db.flush()
        _refresh_route_path(db, route)
    db.commit()
    db.refresh(stop)
    return _stop_to_read(stop)


@router.delete("/{route_id}/stops/{stop_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stop(
    route_id: int,
    stop_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> None:
    """Los estudiantes asignados a esta parada quedan sin parada (FK con
    ON DELETE SET NULL), no se borran."""
    route = _get_owned_route(db, tenant_id, route_id)
    stop = _get_owned_stop(db, tenant_id, route_id, stop_id)
    db.delete(stop)
    db.flush()
    _refresh_route_path(db, route)
    db.commit()


@router.get("/{route_id}/path", response_model=RoutePathRead)
def get_route_path(
    route_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> RoutePathRead:
    """Trazado para dibujar en el mapa. Abierto a todos los roles del colegio
    (lectura), igual que las paradas."""
    route = _get_owned_route(db, tenant_id, route_id)
    if route.geom is None:
        stops = _ordered_stops(db, tenant_id, route_id)
        if len(stops) < 2:
            return RoutePathRead(route_id=route.id, coordinates=[], distance_m=0.0, source="none")
        # Rutas creadas antes de esta función (o cuando OSRM estaba caído)
        # no tienen trazado guardado: se intenta calcular ahora mismo.
        _refresh_route_path(db, route)
        db.commit()
        if route.geom is None:
            path = fetch_road_path(_stop_coords(stops))
            return RoutePathRead(
                route_id=route.id,
                coordinates=[[lon, lat] for lon, lat in path.coordinates],
                distance_m=round(path.distance_m, 1),
                source=path.source,
            )
    coords = [(c[0], c[1]) for c in to_shape(route.geom).coords]
    return RoutePathRead(
        route_id=route.id,
        coordinates=[[lon, lat] for lon, lat in coords],
        distance_m=round(polyline_length_m(coords), 1),
        source="osrm",
    )


@router.get("/{route_id}/optimization", response_model=RouteOptimizationRead)
def get_route_optimization(
    route_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> RouteOptimizationRead:
    """Propone el mejor orden de paradas (OSRM resuelve el problema del
    viajante con la primera parada y el colegio fijos). Solo consulta: para
    aplicarlo, el panel llama a `PUT .../stops/order` con `proposed_stop_ids`."""
    _get_owned_route(db, tenant_id, route_id)
    stops = _ordered_stops(db, tenant_id, route_id)
    coords = _stop_coords(stops)
    current_ids = [s.id for s in stops]
    current_distance = _drawn_length_m(coords)

    proposal = propose_stop_order(coords)
    proposed_ids = [current_ids[i] for i in proposal.order]
    proposed_distance = current_distance
    changed = False
    if proposal.source == "osrm" and proposed_ids != current_ids:
        # Se mide la propuesta con el MISMO método que la ruta actual (trazado
        # completo por calles) y solo se sugiere si de verdad acorta el
        # recorrido: nunca se le propone al admin un cambio que empeore.
        proposed_distance = _drawn_length_m([coords[i] for i in proposal.order])
        changed = proposed_distance < current_distance - MIN_SAVING_M
    if not changed:
        proposed_ids = current_ids
        proposed_distance = current_distance
    return RouteOptimizationRead(
        route_id=route_id,
        current_stop_ids=current_ids,
        proposed_stop_ids=proposed_ids,
        current_distance_m=round(current_distance, 1),
        proposed_distance_m=round(proposed_distance, 1),
        changed=changed,
        source=proposal.source,
    )
