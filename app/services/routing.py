"""
Cálculo de recorridos por las calles reales usando OSRM (Open Source Routing
Machine, sobre datos de OpenStreetMap).

Se usa el servidor público de demostración de OSRM: no necesita llave ni
facturación, que es lo adecuado para el alcance académico del proyecto. A
cambio, no tiene garantía de disponibilidad, así que TODO aquí está diseñado
para degradar con elegancia:

- Si OSRM no responde, `fetch_road_path` devuelve líneas rectas entre paradas
  (marcado con `source="straight_line"`) en vez de lanzar una excepción.
- El trazado bueno (el de OSRM) se guarda en `Route.geom`, así que una caída
  de OSRM durante la demo no borra lo que ya se había calculado.

Coordenadas siempre en orden (lon, lat), igual que GeoJSON y PostGIS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import httpx

OSRM_BASE_URL = "https://router.project-osrm.org"
# Corto a propósito: esto corre dentro de una petición HTTP del panel admin o
# de la app, y es preferible responder rápido con líneas rectas que dejar al
# usuario esperando a un servidor de demostración caído.
OSRM_TIMEOUT_SECONDS = 8.0

Coord = tuple[float, float]  # (lon, lat)


@dataclass
class RoadPath:
    coordinates: list[Coord]
    distance_m: float
    source: str  # "osrm" | "straight_line"


@dataclass
class StopOrderProposal:
    # Posiciones (índices de la lista de entrada) en el orden óptimo.
    order: list[int]
    distance_m: float
    source: str  # "osrm" | "unchanged"


def haversine_m(a: Coord, b: Coord) -> float:
    """Distancia en metros entre dos puntos (lon, lat) sobre la esfera terrestre."""
    lon1, lat1, lon2, lat2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def polyline_length_m(coords: list[Coord]) -> float:
    return sum(haversine_m(coords[i], coords[i + 1]) for i in range(len(coords) - 1))


def _coords_param(coords: list[Coord]) -> str:
    return ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in coords)


def _straight_path(coords: list[Coord]) -> RoadPath:
    return RoadPath(coordinates=list(coords), distance_m=polyline_length_m(coords), source="straight_line")


def fetch_road_path(coords: list[Coord]) -> RoadPath:
    """Trazado por calles que pasa por las paradas EN EL ORDEN DADO."""
    if len(coords) < 2:
        return _straight_path(coords)
    try:
        response = httpx.get(
            f"{OSRM_BASE_URL}/route/v1/driving/{_coords_param(coords)}",
            params={"overview": "full", "geometries": "geojson"},
            timeout=OSRM_TIMEOUT_SECONDS,
        )
        data = response.json()
        if response.status_code != 200 or data.get("code") != "Ok":
            return _straight_path(coords)
        route = data["routes"][0]
        return RoadPath(
            coordinates=[(c[0], c[1]) for c in route["geometry"]["coordinates"]],
            distance_m=float(route["distance"]),
            source="osrm",
        )
    except Exception:
        return _straight_path(coords)


# Hasta este número de paradas intermedias se prueban TODOS los órdenes
# posibles (7! = 5040 combinaciones, instantáneo). Por encima, se usa el
# servicio /trip de OSRM, que es heurístico y no garantiza el óptimo.
MAX_STOPS_FOR_EXACT_SEARCH = 7


def _distance_matrix(coords: list[Coord]) -> list[list[float]] | None:
    """Distancias POR CALLES (metros) entre cada par de paradas, vía OSRM /table."""
    try:
        response = httpx.get(
            f"{OSRM_BASE_URL}/table/v1/driving/{_coords_param(coords)}",
            params={"annotations": "distance"},
            timeout=OSRM_TIMEOUT_SECONDS,
        )
        data = response.json()
        if response.status_code != 200 or data.get("code") != "Ok":
            return None
        matrix = data["distances"]
        if any(value is None for row in matrix for value in row):
            return None  # algún par sin camino posible por calles
        return matrix
    except Exception:
        return None


def _best_order_exact(matrix: list[list[float]]) -> tuple[list[int], float]:
    """Problema del viajante por fuerza bruta con inicio y fin fijos."""
    from itertools import permutations

    last = len(matrix) - 1
    best_order, best_distance = None, math.inf
    for middle in permutations(range(1, last)):
        order = [0, *middle, last]
        distance = sum(matrix[order[i]][order[i + 1]] for i in range(last))
        if distance < best_distance:
            best_order, best_distance = order, distance
    return best_order, best_distance


def propose_stop_order(coords: list[Coord]) -> StopOrderProposal:
    """Mejor orden para visitar las paradas (problema del viajante).

    La PRIMERA y la ÚLTIMA parada quedan fijas: la primera es donde arranca el
    bus y la última es el colegio, así que solo se reordenan las intermedias.
    Con menos de 3 paradas no hay nada que reordenar.

    Con pocas paradas se calcula el óptimo EXACTO sobre la matriz de
    distancias reales por calles; con muchas, se usa la heurística de OSRM."""
    identity = list(range(len(coords)))
    if len(coords) < 3:
        return StopOrderProposal(order=identity, distance_m=0.0, source="unchanged")
    if len(coords) - 2 <= MAX_STOPS_FOR_EXACT_SEARCH:
        matrix = _distance_matrix(coords)
        if matrix is None:
            return StopOrderProposal(order=identity, distance_m=0.0, source="unchanged")
        order, distance = _best_order_exact(matrix)
        return StopOrderProposal(order=order, distance_m=distance, source="osrm")
    try:
        response = httpx.get(
            f"{OSRM_BASE_URL}/trip/v1/driving/{_coords_param(coords)}",
            params={"source": "first", "destination": "last", "roundtrip": "false", "overview": "false"},
            timeout=OSRM_TIMEOUT_SECONDS,
        )
        data = response.json()
        if response.status_code != 200 or data.get("code") != "Ok":
            return StopOrderProposal(order=identity, distance_m=0.0, source="unchanged")
        # OSRM devuelve, para cada punto de entrada, en qué posición del viaje
        # óptimo queda (`waypoint_index`); se invierte para obtener el orden.
        positions = [w["waypoint_index"] for w in data["waypoints"]]
        order = sorted(identity, key=lambda i: positions[i])
        return StopOrderProposal(order=order, distance_m=float(data["trips"][0]["distance"]), source="osrm")
    except Exception:
        return StopOrderProposal(order=identity, distance_m=0.0, source="unchanged")
