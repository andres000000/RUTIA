"""
Búsqueda de direcciones (geocodificación) con Nominatim, el buscador gratuito
de OpenStreetMap -- el mismo proyecto de datos que usa OSRM para las rutas.

Las direcciones colombianas ("Calle 45 # 27-10") no siempre se encuentran
exactas en OpenStreetMap, así que esto es solo el PRIMER paso: el panel
muestra el resultado en un mapa y el admin arrastra el pin hasta la casa
exacta. Por eso aquí basta con acercarse a la cuadra correcta.

Política de uso de Nominatim: máximo ~1 petición por segundo e identificarse
con un User-Agent propio. Solo lo usa el admin al registrar una dirección,
así que el volumen es mínimo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "RUTIA/1.0 (proyecto academico USTA Bucaramanga)"
TIMEOUT_SECONDS = 8.0

# Área metropolitana de Bucaramanga (Bucaramanga, Floridablanca, Girón,
# Piedecuesta): los resultados se limitan a esta caja para no confundir una
# "Calle 45" de Bucaramanga con la de otra ciudad.
METRO_VIEWBOX = "-73.25,7.20,-73.00,6.95"  # lon_min, lat_max, lon_max, lat_min


@dataclass
class GeocodeResult:
    display_name: str
    lat: float
    lon: float


def _normalize(address: str) -> str:
    """Quita la notación "#"/"No." de las direcciones colombianas, que confunde
    al buscador: "Calle 45 # 27-10" se busca como "Calle 45 27-10"."""
    cleaned = re.sub(r"(#|\bN[oº°]\.?)", " ", address, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", cleaned).strip()


def search_address(address: str, limit: int = 5) -> list[GeocodeResult]:
    """Devuelve hasta `limit` coincidencias; lista vacía si no hay o si el
    servicio no responde (nunca lanza excepción)."""
    query = _normalize(address)
    if not query:
        return []
    try:
        response = httpx.get(
            NOMINATIM_URL,
            params={
                "q": query,
                "format": "jsonv2",
                "countrycodes": "co",
                "viewbox": METRO_VIEWBOX,
                "bounded": 1,
                "limit": limit,
            },
            headers={"User-Agent": USER_AGENT, "Accept-Language": "es"},
            timeout=TIMEOUT_SECONDS,
        )
        if response.status_code != 200:
            return []
        return [
            GeocodeResult(display_name=item["display_name"], lat=float(item["lat"]), lon=float(item["lon"]))
            for item in response.json()
        ]
    except Exception:
        return []
