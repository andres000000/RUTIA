from datetime import time as time_type

from pydantic import BaseModel, Field


class PointIn(BaseModel):
    """Coordenada geográfica en formato [longitud, latitud] (orden estándar GeoJSON)."""

    lon: float
    lat: float


class StopCreate(BaseModel):
    name: str
    order_index: int = 0
    location: PointIn
    geofence_radius_m: int = Field(100, ge=10, le=2000)


class StopUpdate(BaseModel):
    """Edición parcial de una parada (el orden se cambia aparte, con
    `StopOrderUpdate`, porque mover una parada afecta a todas las demás)."""

    name: str | None = None
    location: PointIn | None = None
    geofence_radius_m: int | None = Field(None, ge=10, le=2000)


class StopOrderUpdate(BaseModel):
    """Lista COMPLETA de ids de paradas de la ruta, en el orden nuevo."""

    stop_ids: list[int]


class RoutePathRead(BaseModel):
    """Trazado de la ruta para dibujarlo en el mapa.

    `coordinates` va en orden [lon, lat] (GeoJSON). `source` dice de dónde salió:
    "osrm" (por calles reales), "straight_line" (OSRM no respondió: líneas
    rectas entre paradas) o "none" (la ruta tiene menos de 2 paradas)."""

    route_id: int
    coordinates: list[list[float]]
    distance_m: float
    source: str


class RouteOptimizationRead(BaseModel):
    """Propuesta de mejor orden de paradas. NO cambia nada por sí sola: el
    panel la muestra y, si el admin la acepta, la aplica con `PUT .../stops/order`."""

    route_id: int
    current_stop_ids: list[int]
    proposed_stop_ids: list[int]
    current_distance_m: float
    proposed_distance_m: float
    changed: bool
    source: str  # "osrm" | "unchanged"


class StopRead(BaseModel):
    id: int
    route_id: int
    name: str
    order_index: int
    geofence_radius_m: int
    lon: float
    lat: float


class RouteCreate(BaseModel):
    name: str
    vehicle_id: int | None = None
    scheduled_start_time: time_type | None = None


class RouteUpdate(BaseModel):
    name: str | None = None
    vehicle_id: int | None = None
    scheduled_start_time: time_type | None = None


class RouteRead(BaseModel):
    id: int
    tenant_id: int
    name: str
    vehicle_id: int | None = None
    scheduled_start_time: time_type | None = None

    model_config = {"from_attributes": True}
