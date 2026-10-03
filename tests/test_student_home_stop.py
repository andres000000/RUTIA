"""La dirección del estudiante se convierte en su parada dentro del recorrido."""

from tests.test_auth_and_crud import _bootstrap_tenant, _login
from tests.test_route_paths import osrm_online  # noqa: F401  (fixture)


def _setup(client):
    _bootstrap_tenant(client)
    headers = {"Authorization": f"Bearer {_login(client, 'admin@test.edu.co', 'clave1234')}"}
    route_id = client.post("/api/v1/routes", json={"name": "Ruta 1"}, headers=headers).json()["id"]
    for index, (name, lon, lat) in enumerate([("Salida", -73.13, 7.11), ("Colegio", -73.10, 7.13)], start=1):
        client.post(
            f"/api/v1/routes/{route_id}/stops",
            json={"name": name, "order_index": index, "location": {"lon": lon, "lat": lat}},
            headers=headers,
        )
    return headers, route_id


def _stops(client, headers, route_id):
    return client.get(f"/api/v1/routes/{route_id}/stops", headers=headers).json()


def test_address_creates_home_stop_before_school(client, osrm_online):  # noqa: F811
    headers, route_id = _setup(client)

    resp = client.post(
        "/api/v1/students",
        json={
            "full_name": "Sofía Pérez",
            "route_id": route_id,
            "address": "Calle 45 # 27-10",
            "home_location": {"lon": -73.115, "lat": 7.12},
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    student = resp.json()
    assert student["address"] == "Calle 45 # 27-10"
    assert (student["home_lat"], student["home_lon"]) == (7.12, -73.115)

    stops = _stops(client, headers, route_id)
    assert [s["name"] for s in stops] == ["Salida", "Sofía Pérez — Calle 45 # 27-10", "Colegio"]
    home = stops[1]
    assert home["student_id"] == student["id"]
    assert student["stop_id"] == home["id"]
    # El trazado ya incluye la casa (3 paradas → 5 puntos con el OSRM simulado).
    assert len(client.get(f"/api/v1/routes/{route_id}/path", headers=headers).json()["coordinates"]) == 5


def test_editing_address_moves_the_same_stop(client):
    headers, route_id = _setup(client)
    student = client.post(
        "/api/v1/students",
        json={"full_name": "Ana", "route_id": route_id, "address": "Cra 1", "home_location": {"lon": -73.12, "lat": 7.12}},
        headers=headers,
    ).json()

    resp = client.put(
        f"/api/v1/students/{student['id']}",
        json={"address": "Cra 2", "home_location": {"lon": -73.11, "lat": 7.125}},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    stops = _stops(client, headers, route_id)
    assert len(stops) == 3  # se movió la misma parada, no se creó otra
    assert stops[1]["name"] == "Ana — Cra 2"
    assert (stops[1]["lat"], stops[1]["lon"]) == (7.125, -73.11)


def test_editing_grade_does_not_touch_stops(client):
    headers, route_id = _setup(client)
    student = client.post(
        "/api/v1/students",
        json={"full_name": "Ana", "route_id": route_id, "address": "Cra 1", "home_location": {"lon": -73.12, "lat": 7.12}},
        headers=headers,
    ).json()
    before = _stops(client, headers, route_id)

    client.put(f"/api/v1/students/{student['id']}", json={"grade": "5B"}, headers=headers)
    assert _stops(client, headers, route_id) == before


def test_changing_route_moves_home_stop(client):
    headers, route_id = _setup(client)
    other_route = client.post("/api/v1/routes", json={"name": "Ruta 2"}, headers=headers).json()["id"]
    client.post(
        f"/api/v1/routes/{other_route}/stops",
        json={"name": "Colegio", "location": {"lon": -73.10, "lat": 7.13}},
        headers=headers,
    )
    student = client.post(
        "/api/v1/students",
        json={"full_name": "Ana", "route_id": route_id, "address": "Cra 1", "home_location": {"lon": -73.12, "lat": 7.12}},
        headers=headers,
    ).json()

    client.put(f"/api/v1/students/{student['id']}", json={"route_id": other_route}, headers=headers)
    assert [s["name"] for s in _stops(client, headers, route_id)] == ["Salida", "Colegio"]
    assert [s["name"] for s in _stops(client, headers, other_route)] == ["Ana — Cra 1", "Colegio"]


def test_removing_route_or_deleting_student_removes_home_stop(client):
    headers, route_id = _setup(client)
    payload = {"full_name": "Ana", "route_id": route_id, "address": "Cra 1", "home_location": {"lon": -73.12, "lat": 7.12}}
    a = client.post("/api/v1/students", json=payload, headers=headers).json()
    b = client.post("/api/v1/students", json={**payload, "full_name": "Beto"}, headers=headers).json()
    assert len(_stops(client, headers, route_id)) == 4

    resp = client.put(f"/api/v1/students/{a['id']}", json={"route_id": None}, headers=headers)
    assert resp.json()["stop_id"] is None
    assert len(_stops(client, headers, route_id)) == 3

    assert client.delete(f"/api/v1/students/{b['id']}", headers=headers).status_code == 204
    stops = _stops(client, headers, route_id)
    assert [s["name"] for s in stops] == ["Salida", "Colegio"]
    assert [s["order_index"] for s in stops] == [1, 2]


def test_student_without_address_keeps_shared_stop(client):
    """Compatibilidad: asignar una parada compartida (como el seed) sigue funcionando."""
    headers, route_id = _setup(client)
    shared = _stops(client, headers, route_id)[0]
    student = client.post(
        "/api/v1/students",
        json={"full_name": "Ana", "route_id": route_id, "stop_id": shared["id"]},
        headers=headers,
    ).json()
    assert student["stop_id"] == shared["id"]
    assert student["address"] is None and student["home_lat"] is None
    assert len(_stops(client, headers, route_id)) == 2


def test_route_from_other_school_is_rejected(client):
    headers, _ = _setup(client)
    _bootstrap_tenant(client, name="Otro", email="admin@otro.edu.co", password="otraclave1")
    other = {"Authorization": f"Bearer {_login(client, 'admin@otro.edu.co', 'otraclave1')}"}
    foreign_route = client.post("/api/v1/routes", json={"name": "Ajena"}, headers=other).json()["id"]

    resp = client.post(
        "/api/v1/students",
        json={"full_name": "Ana", "route_id": foreign_route, "home_location": {"lon": -73.12, "lat": 7.12}},
        headers=headers,
    )
    assert resp.status_code == 404


def test_geocode_is_admin_only_and_never_fails(client, monkeypatch):
    headers, _ = _setup(client)
    monkeypatch.setattr("app.services.geocoding.httpx.get", lambda *a, **k: (_ for _ in ()).throw(ConnectionError()))
    resp = client.get("/api/v1/geocode", params={"q": "Calle 45 # 27-10"}, headers=headers)
    assert resp.status_code == 200
    assert resp.json() == []  # sin servicio → lista vacía, no error 500
    assert client.get("/api/v1/geocode", params={"q": "Calle 45"}).status_code == 401
