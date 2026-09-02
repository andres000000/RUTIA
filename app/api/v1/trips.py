from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from geoalchemy2.shape import to_shape
from sqlalchemy.orm import Session

from app.api.deps import get_current_tenant_id, require_roles
from app.core.database import get_db
from app.ml.predict import predict_trip_duration_minutes
from app.models.gps_position import GPSPosition
from app.models.route import Route
from app.models.trip import Trip, TripStatus
from app.models.user import Role, User
from app.models.vehicle import Vehicle
from app.schemas.trip import GPSPositionRead, TripCreate, TripEtaRead, TripRead

router = APIRouter(prefix="/trips", tags=["trips"])


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
