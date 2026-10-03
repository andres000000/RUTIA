from pydantic import BaseModel

from app.schemas.route import PointIn


class StudentCreate(BaseModel):
    full_name: str
    grade: str | None = None
    parent_id: int | None = None
    route_id: int | None = None
    stop_id: int | None = None
    # Dirección de la casa + su ubicación en el mapa (la que el admin confirmó
    # arrastrando el pin). Si vienen junto con una ruta, se crea la parada "de
    # casa" del estudiante dentro de esa ruta y se le asigna (ignora stop_id).
    address: str | None = None
    home_location: PointIn | None = None


class StudentUpdate(BaseModel):
    full_name: str | None = None
    grade: str | None = None
    parent_id: int | None = None
    route_id: int | None = None
    stop_id: int | None = None
    is_active: bool | None = None
    address: str | None = None
    home_location: PointIn | None = None


class StudentRead(BaseModel):
    id: int
    tenant_id: int
    full_name: str
    grade: str | None = None
    parent_id: int | None = None
    route_id: int | None = None
    stop_id: int | None = None
    is_active: bool
    address: str | None = None
    # Ubicación de la parada de casa, si el estudiante tiene una (para que el
    # panel pueda volver a mostrar el pin al editar).
    home_lat: float | None = None
    home_lon: float | None = None
