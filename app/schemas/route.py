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
