from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import require_roles
from app.models.user import Role
from app.services.geocoding import search_address

router = APIRouter(prefix="/geocode", tags=["geocode"])


class GeocodeResultRead(BaseModel):
    display_name: str
    lat: float
    lon: float


@router.get("", response_model=list[GeocodeResultRead])
def geocode(
    q: str = Query(..., min_length=3, max_length=200),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> list[GeocodeResultRead]:
    """Busca una dirección en el área metropolitana de Bucaramanga. Solo para
    ADMIN (es quien registra las direcciones de los estudiantes) y pasa por el
    backend en vez de llamar a Nominatim desde el navegador para controlar el
    User-Agent y el volumen que exige su política de uso."""
    return [GeocodeResultRead(**r.__dict__) for r in search_address(q)]
