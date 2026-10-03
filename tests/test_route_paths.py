"""Paradas editables, trazado por calles (OSRM) y propuesta de mejor orden."""

import pytest

from app.services import routing
from tests.test_auth_and_crud import _bootstrap_tenant, _login

# Tres paradas en Bucaramanga, en un orden deliberadamente malo: la del medio
# (C) queda geográficamente al final, así que el orden óptimo es A, B, C, D.
STOPS = [
    ("A - Salida", -73.1300, 7.1100),
    ("C - Lejos", -73.1100, 7.1300),
    ("B - Cerca", -73.1250, 7.1150),
    ("D - Colegio", -73.1050, 7.1350),
]


class _FakeResponse:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload

    def json(self):
        return self._payload


def _fake_osrm_get(url, params=None, timeout=None):
    """Simula OSRM con distancias en línea recta (haversine) como si fueran
    distancias por calles: /route devuelve un trazado con un punto intermedio
    extra entre cada par de paradas, /table la matriz de distancias, y /trip
    propone visitar las paradas de oeste a este."""
    coords_part = url.rsplit("/", 1)[-1]
    coords = [tuple(map(float, c.split(","))) for c in coords_part.split(";")]
    if "/route/" in url:
        geometry = []
        for (lon1, lat1), (lon2, lat2) in zip(coords, coords[1:]):
            geometry += [[lon1, lat1], [(lon1 + lon2) / 2, (lat1 + lat2) / 2]]
        geometry.append(list(coords[-1]))
        distance = routing.polyline_length_m(coords)
        return _FakeResponse(
            {"code": "Ok", "routes": [{"distance": distance, "geometry": {"coordinates": geometry}}]}
        )
    if "/table/" in url:
        return _FakeResponse(
            {"code": "Ok", "distances": [[routing.haversine_m(a, b) for b in coords] for a in coords]}
        )
    ranking = sorted(range(len(coords)), key=lambda i: coords[i][0])
    return _FakeResponse(
        {
            "code": "Ok",
            "waypoints": [{"waypoint_index": ranking.index(i)} for i in range(len(coords))],
            "trips": [{"distance": 3200.0}],
        }
    )


@pytest.fixture
def osrm_online(monkeypatch):
    monkeypatch.setattr(routing.httpx, "get", _fake_osrm_get)


def _admin_headers(client):
    _bootstrap_tenant(client)
    return {"Authorization": f"Bearer {_login(client, 'admin@test.edu.co', 'clave1234')}"}


def _route_with_stops(client, headers):
    route_id = client.post("/api/v1/routes", json={"name": "Ruta mapa"}, headers=headers).json()["id"]
    ids = []
    for index, (name, lon, lat) in enumerate(STOPS, start=1):
        resp = client.post(
            f"/api/v1/routes/{route_id}/stops",
            json={"name": name, "order_index": index, "location": {"lon": lon, "lat": lat}},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    return route_id, ids


def test_path_follows_streets_when_osrm_responds(client, osrm_online):
    headers = _admin_headers(client)
    route_id, _ = _route_with_stops(client, headers)

    body = client.get(f"/api/v1/routes/{route_id}/path", headers=headers).json()
    assert body["source"] == "osrm"
    # 4 paradas + 3 puntos intermedios del trazado simulado
    assert len(body["coordinates"]) == 7
    assert body["coordinates"][0] == [-73.13, 7.11]
    assert body["distance_m"] > 0


def test_path_falls_back_to_straight_lines_when_osrm_is_down(client):
    headers = _admin_headers(client)
    route_id, _ = _route_with_stops(client, headers)

    body = client.get(f"/api/v1/routes/{route_id}/path", headers=headers).json()
    assert body["source"] == "straight_line"
    assert body["coordinates"] == [[lon, lat] for _, lon, lat in STOPS]


def test_path_needs_at_least_two_stops(client):
    headers = _admin_headers(client)
    route_id = client.post("/api/v1/routes", json={"name": "Vacía"}, headers=headers).json()["id"]

    body = client.get(f"/api/v1/routes/{route_id}/path", headers=headers).json()
    assert body == {"route_id": route_id, "coordinates": [], "distance_m": 0.0, "source": "none"}


def test_update_reorder_and_delete_stops(client, osrm_online):
    headers = _admin_headers(client)
    route_id, ids = _route_with_stops(client, headers)

    moved = client.put(
        f"/api/v1/routes/{route_id}/stops/{ids[0]}",
        json={"name": "A - Salida nueva", "location": {"lon": -73.131, "lat": 7.109}},
        headers=headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["name"] == "A - Salida nueva"
    assert moved.json()["lon"] == -73.131

    new_order = [ids[0], ids[2], ids[1], ids[3]]
    reordered = client.put(f"/api/v1/routes/{route_id}/stops/order", json={"stop_ids": new_order}, headers=headers)
    assert reordered.status_code == 200, reordered.text
    assert [s["id"] for s in reordered.json()] == new_order
    assert [s["order_index"] for s in reordered.json()] == [1, 2, 3, 4]

    assert client.delete(f"/api/v1/routes/{route_id}/stops/{ids[2]}", headers=headers).status_code == 204
    remaining = client.get(f"/api/v1/routes/{route_id}/stops", headers=headers).json()
    assert [s["id"] for s in remaining] == [ids[0], ids[1], ids[3]]
    assert [s["order_index"] for s in remaining] == [1, 2, 3]  # se renumeran sin huecos


def test_reorder_rejects_incomplete_list(client):
    headers = _admin_headers(client)
    route_id, ids = _route_with_stops(client, headers)

    resp = client.put(f"/api/v1/routes/{route_id}/stops/order", json={"stop_ids": ids[:2]}, headers=headers)
    assert resp.status_code == 422


def test_optimization_proposes_better_order_without_applying_it(client, osrm_online):
    headers = _admin_headers(client)
    route_id, ids = _route_with_stops(client, headers)

    body = client.get(f"/api/v1/routes/{route_id}/optimization", headers=headers).json()
    assert body["source"] == "osrm"
    assert body["changed"] is True
    assert body["current_stop_ids"] == ids
    # Primera y última fijas; las intermedias se intercambian (B antes que C).
    assert body["proposed_stop_ids"] == [ids[0], ids[2], ids[1], ids[3]]
    assert body["proposed_distance_m"] < body["current_distance_m"]

    # Solo es una propuesta: el orden guardado no cambia hasta aplicarla.
    stops = client.get(f"/api/v1/routes/{route_id}/stops", headers=headers).json()
    assert [s["id"] for s in stops] == ids


def test_exact_search_finds_optimum_for_few_stops():
    # Matriz donde el orden óptimo es 0 → 2 → 1 → 3 (total 3), y cualquier
    # otro orden cuesta más.
    matrix = [
        [0, 10, 1, 10],
        [10, 0, 1, 1],
        [1, 1, 0, 10],
        [10, 1, 10, 0],
    ]
    order, distance = routing._best_order_exact(matrix)
    assert order == [0, 2, 1, 3]
    assert distance == 3


def test_optimization_never_proposes_a_longer_route(client, osrm_online, monkeypatch):
    """Si el algoritmo devolviera un orden peor (como pasa a veces con la
    heurística /trip de OSRM), el panel no debe sugerirlo."""
    headers = _admin_headers(client)
    route_id, ids = _route_with_stops(client, headers)
    # Primero se aplica el orden bueno, y luego se simula una propuesta peor.
    client.put(
        f"/api/v1/routes/{route_id}/stops/order",
        json={"stop_ids": [ids[0], ids[2], ids[1], ids[3]]},
        headers=headers,
    )
    monkeypatch.setattr(
        "app.api.v1.routes.propose_stop_order",
        lambda coords: routing.StopOrderProposal(order=[0, 2, 1, 3], distance_m=1.0, source="osrm"),
    )

    body = client.get(f"/api/v1/routes/{route_id}/optimization", headers=headers).json()
    assert body["changed"] is False
    assert body["proposed_stop_ids"] == body["current_stop_ids"]


def test_optimization_is_unchanged_when_osrm_is_down(client):
    headers = _admin_headers(client)
    route_id, ids = _route_with_stops(client, headers)

    body = client.get(f"/api/v1/routes/{route_id}/optimization", headers=headers).json()
    assert body["changed"] is False
    assert body["proposed_stop_ids"] == ids


def test_non_admin_can_read_path_but_not_edit_stops(client):
    headers = _admin_headers(client)
    route_id, ids = _route_with_stops(client, headers)
    client.post(
        "/api/v1/users",
        json={"email": "padre@test.edu.co", "password": "clave1234", "full_name": "Padre", "role": "PADRE"},
        headers=headers,
    )
    parent = {"Authorization": f"Bearer {_login(client, 'padre@test.edu.co', 'clave1234')}"}

    assert client.get(f"/api/v1/routes/{route_id}/path", headers=parent).status_code == 200
    assert client.get(f"/api/v1/routes/{route_id}/optimization", headers=parent).status_code == 403
    assert client.delete(f"/api/v1/routes/{route_id}/stops/{ids[0]}", headers=parent).status_code == 403
    assert (
        client.put(f"/api/v1/routes/{route_id}/stops/order", json={"stop_ids": ids}, headers=parent).status_code
        == 403
    )


def test_other_school_cannot_touch_stops(client):
    headers = _admin_headers(client)
    route_id, ids = _route_with_stops(client, headers)
    _bootstrap_tenant(client, name="Otro", email="admin@otro.edu.co", password="otraclave1")
    other = {"Authorization": f"Bearer {_login(client, 'admin@otro.edu.co', 'otraclave1')}"}

    assert client.get(f"/api/v1/routes/{route_id}/path", headers=other).status_code == 404
    assert client.delete(f"/api/v1/routes/{route_id}/stops/{ids[0]}", headers=other).status_code == 404
