from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel

from app.models.trip import TripStatus
from app.schemas.route import RoutePathRead


class TripCreate(BaseModel):
    route_id: int
    date: date_type


class TripRead(BaseModel):
    id: int
    tenant_id: int
    route_id: int
    vehicle_id: int | None = None
    driver_id: int | None = None
    date: date_type
    status: TripStatus
    start_time: datetime | None = None
    end_time: datetime | None = None

    model_config = {"from_attributes": True}


class GPSPositionRead(BaseModel):
    id: int
    lat: float
    lon: float
    speed_kmh: float | None = None
    heading_deg: float | None = None
    recorded_at: datetime


class TripEtaRead(BaseModel):
    """Predicción de duración del Objetivo 4 (módulo de IA)."""

    trip_id: int
    predicted_duration_minutes: float
    # Solo tiene valor cuando el viaje ya está en curso: cuántos minutos
    # faltan según la predicción, descontando lo que ya lleva andando.
    estimated_remaining_minutes: float | None = None
    # "model" = predicho por el modelo entrenado; "heuristic_historical" =
    # mediana de viajes pasados de esa ruta (sin modelo entrenado todavía);
    # "heuristic_default" = ni modelo ni histórico disponible.
    source: str


class TripTrackRead(BaseModel):
    """Recorrido real grabado de un viaje, junto al trazado planeado de su ruta.

    `coordinates` va en orden [lon, lat] (GeoJSON), igual que el trazado
    planeado, y viene reducido a unos cientos de puntos para dibujarlo rápido;
    las métricas sí se calculan con todas las posiciones grabadas."""

    trip_id: int
    route_id: int
    status: TripStatus
    coordinates: list[list[float]]
    point_count: int
    distance_m: float
    duration_minutes: float | None = None
    avg_speed_kmh: float | None = None
    max_speed_kmh: float | None = None
    # % de posiciones a más de `off_route_threshold_m` del trazado planeado.
    # None si no hay trazado planeado o no hay posiciones con qué comparar.
    off_route_pct: float | None = None
    off_route_threshold_m: float
    planned: RoutePathRead
