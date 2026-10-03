import math
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import to_shape
from shapely.geometry import LineString, Point
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, require_roles
from app.api.v1.routes import build_route_path
from app.core.database import get_db
from app.ml.predict import predict_trip_duration_minutes
from app.models.gps_position import GPSPosition
from app.models.route import Route
from app.models.trip import Trip, TripStatus
from app.models.user import Role, User
from app.models.vehicle import Vehicle
from app.schemas.trip import GPSPositionRead, TripCreate, TripEtaRead, TripRead, TripTrackRead
from app.services.routing import haversine_m

router = APIRouter(prefix="/trips", tags=["trips"])

# Más lejos que esto del trazado planeado cuenta como "fuera de ruta". Holgado
# a propósito: el GPS de un celular tiene errores de 10-30 m y una calle
# paralela queda a ~80-100 m en el centro de Bucaramanga.
OFF_ROUTE_THRESHOLD_M = 120.0
# Un salto entre dos posiciones seguidas que implique más de esto es un error
# del GPS (no el bus): no se suma a la distancia recorrida.
GPS_GLITCH_SPEED_KMH = 150.0
# Puntos máximos del recorrido que se mandan para dibujar.
MAX_TRACK_POINTS = 400


def _get_owned_trip(db: Session, tenant_id: int, trip_id: int) -> Trip:
    trip = db.query(Trip).filter(Trip.id == trip_id, Trip.tenant_id == tenant_id).first()
    if trip is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Viaje no encontrado")
    return trip


@router.get("", response_model=list[TripRead])
def list_trips(
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> list[Trip]:
    return db.query(Trip).filter(Trip.tenant_id == tenant_id).all()


@router.post("", response_model=TripRead, status_code=status.HTTP_201_CREATED)
def create_trip(
    payload: TripCreate,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> Trip:
    """El ADMIN programa un viaje para una fecha dada, a partir de una ruta ya creada.
    El bus y el conductor del viaje se heredan automáticamente de la ruta."""
    route = db.query(Route).filter(Route.id == payload.route_id, Route.tenant_id == tenant_id).first()
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ruta no encontrada")

    driver_id = None
    if route.vehicle_id is not None:
        vehicle = db.get(Vehicle, route.vehicle_id)
        driver_id = vehicle.driver_id if vehicle else None

    trip = Trip(
        tenant_id=tenant_id,
        route_id=route.id,
        vehicle_id=route.vehicle_id,
        driver_id=driver_id,
        date=payload.date,
    )
    db.add(trip)
    db.commit()
    db.refresh(trip)
    return trip


@router.delete("/{trip_id}", status_code=status.HTTP_204_NO_CONTENT)
def cancel_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> None:
    """El ADMIN cancela/borra un viaje que programó por error. Solo se permite
    mientras el viaje sigue en SCHEDULED -- una vez el conductor lo inició (o
    ya terminó), borrarlo destruiría el histórico real de posiciones GPS que
    usa el módulo de IA (Objetivo 4) para entrenar, así que en ese caso hay
    que dejarlo como CANCELLED en vez de eliminarlo... pero como todavía no
    hay ningún caso de uso que necesite "cancelar a medio viaje" (el conductor
    ya lo estaría transmitiendo en vivo), por ahora simplemente no se permite."""
    trip = _get_owned_trip(db, tenant_id, trip_id)
    if trip.status != TripStatus.SCHEDULED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Solo se pueden cancelar viajes que todavía no han iniciado",
        )
    db.delete(trip)
    db.commit()


@router.post("/{trip_id}/start", response_model=TripRead)
def start_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    current_user: User = Depends(require_roles(Role.CONDUCTOR)),
) -> Trip:
    trip = _get_owned_trip(db, tenant_id, trip_id)
    if trip.driver_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No eres el conductor asignado a este viaje"
        )
    trip.status = TripStatus.IN_PROGRESS
    trip.start_time = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trip)
    return trip


@router.post("/{trip_id}/end", response_model=TripRead)
def end_trip(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    current_user: User = Depends(require_roles(Role.CONDUCTOR)),
) -> Trip:
    trip = _get_owned_trip(db, tenant_id, trip_id)
    if trip.driver_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No eres el conductor asignado a este viaje"
        )
    trip.status = TripStatus.COMPLETED
    trip.end_time = datetime.now(timezone.utc)
    db.commit()
    db.refresh(trip)
    return trip


@router.get("/{trip_id}/eta", response_model=TripEtaRead)
def get_trip_eta(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> TripEtaRead:
    """Predicción de duración del viaje (Objetivo 4: módulo de IA).

    Cualquier usuario del colegio puede consultarla (padre, monitor, conductor,
    admin) — es información de lectura, no una acción sobre el viaje."""
    trip = _get_owned_trip(db, tenant_id, trip_id)

    if trip.start_time is not None:
        reference_start = trip.start_time
    else:
        route = db.get(Route, trip.route_id)
        scheduled_time = route.scheduled_start_time if route else None
        if scheduled_time is not None:
            reference_start = datetime.combine(trip.date, scheduled_time, tzinfo=timezone.utc)
        else:
            # Sin viaje iniciado y sin horario programado en la ruta: la mejor
            # referencia disponible es "si saliera ahora mismo".
            reference_start = datetime.now(timezone.utc)

    prediction = predict_trip_duration_minutes(db, trip.route_id, reference_start)

    estimated_remaining_minutes = None
    if trip.status == TripStatus.IN_PROGRESS and trip.start_time is not None:
        elapsed_minutes = (datetime.now(timezone.utc) - trip.start_time).total_seconds() / 60
        estimated_remaining_minutes = round(
            max(prediction.predicted_duration_minutes - elapsed_minutes, 0), 1
        )

    return TripEtaRead(
        trip_id=trip.id,
        predicted_duration_minutes=prediction.predicted_duration_minutes,
        estimated_remaining_minutes=estimated_remaining_minutes,
        source=prediction.source,
    )


@router.get("/{trip_id}/positions", response_model=list[GPSPositionRead])
def list_positions(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
) -> list[GPSPositionRead]:
    """Histórico de posiciones de un viaje. Útil para depurar el WebSocket y, más
    adelante, como fuente de datos para el módulo de IA (Objetivo 4)."""
    trip = _get_owned_trip(db, tenant_id, trip_id)
    positions = (
        db.query(GPSPosition)
        .filter(GPSPosition.trip_id == trip.id)
        .order_by(GPSPosition.recorded_at)
        .all()
    )
    result = []
    for p in positions:
        point = to_shape(p.geom)
        result.append(
            GPSPositionRead(
                id=p.id,
                lat=point.y,
                lon=point.x,
                speed_kmh=p.speed_kmh,
                heading_deg=p.heading_deg,
                recorded_at=p.recorded_at,
            )
        )
    return result


def _to_local_meters(lon: float, lat: float, ref_lat: float) -> tuple[float, float]:
    """Proyección plana aproximada (equirectangular) en metros. Para distancias
    de una ciudad el error es despreciable y evita depender de PostGIS aquí."""
    return (lon * 111_320 * math.cos(math.radians(ref_lat)), lat * 110_540)


def _downsample(coords: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
    if len(coords) <= limit:
        return coords
    step = math.ceil(len(coords) / limit)
    sampled = coords[::step]
    if sampled[-1] != coords[-1]:
        sampled.append(coords[-1])  # el punto final siempre se dibuja
    return sampled


@router.get("/{trip_id}/track", response_model=TripTrackRead)
def get_trip_track(
    trip_id: int,
    db: Session = Depends(get_db),
    tenant_id: int = Depends(get_current_tenant_id),
    _user=Depends(require_roles(Role.ADMIN, Role.MONITOR, Role.CONDUCTOR)),
) -> TripTrackRead:
    """Recorrido real vs. planeado de un viaje (panel admin e Historial del
    monitor/conductor). El PADRE queda fuera a propósito: en la app solo ve
    su propia parada y el bus, no el recorrido completo de la ruta.

    Ojo: el trazado planeado es el de la ruta HOY; si el admin cambió las
    paradas después del viaje, la comparación es contra la ruta nueva."""
    trip = _get_owned_trip(db, tenant_id, trip_id)
    route = db.query(Route).filter(Route.id == trip.route_id, Route.tenant_id == tenant_id).first()
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ruta no encontrada")
    planned = build_route_path(db, route)

    positions = (
        db.query(GPSPosition)
        .filter(GPSPosition.trip_id == trip.id)
        .order_by(GPSPosition.recorded_at)
        .all()
    )
    coords: list[tuple[float, float]] = []
    for p in positions:
        point = to_shape(p.geom)
        coords.append((point.x, point.y))

    distance_m = 0.0
    for prev, curr, a, b in zip(positions, positions[1:], coords, coords[1:]):
        step_m = haversine_m(a, b)
        seconds = (curr.recorded_at - prev.recorded_at).total_seconds()
        if seconds > 0 and step_m / seconds * 3.6 > GPS_GLITCH_SPEED_KMH:
            continue
        distance_m += step_m

    duration_minutes = None
    avg_speed_kmh = None
    if len(positions) >= 2:
        seconds = (positions[-1].recorded_at - positions[0].recorded_at).total_seconds()
        duration_minutes = round(seconds / 60, 1)
        if seconds > 0:
            avg_speed_kmh = round(distance_m / seconds * 3.6, 1)
    speeds = [p.speed_kmh for p in positions if p.speed_kmh is not None]
    max_speed_kmh = round(max(speeds), 1) if speeds else None

    off_route_pct = None
    if coords and len(planned.coordinates) >= 2:
        ref_lat = coords[0][1]
        line = LineString([_to_local_meters(lon, lat, ref_lat) for lon, lat in planned.coordinates])
        off = sum(
            1 for lon, lat in coords if line.distance(Point(_to_local_meters(lon, lat, ref_lat))) > OFF_ROUTE_THRESHOLD_M
        )
        off_route_pct = round(off / len(coords) * 100, 1)

    return TripTrackRead(
        trip_id=trip.id,
        route_id=route.id,
        status=trip.status,
        coordinates=[[lon, lat] for lon, lat in _downsample(coords, MAX_TRACK_POINTS)],
        point_count=len(positions),
        distance_m=round(distance_m, 1),
        duration_minutes=duration_minutes,
        avg_speed_kmh=avg_speed_kmh,
        max_speed_kmh=max_speed_kmh,
        off_route_pct=off_route_pct,
        off_route_threshold_m=OFF_ROUTE_THRESHOLD_M,
        planned=planned,
    )
