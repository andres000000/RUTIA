"""Recorrido real grabado vs. trazado planeado (GET /trips/{id}/track)."""

import datetime as dt

from app.models.gps_position import GPSPosition
from tests.test_trips_and_ws import _bootstrap_tenant, _login

# Ruta recta de oeste a este (~2.2 km). Sin red en las pruebas, el trazado
# planeado queda en línea recta entre las paradas.
STOPS = [("Salida", -73.1300, 7.1100), ("Colegio", -73.1100, 7.1100)]


def _setup(client):
    _bootstrap_tenant(client, "Colegio Track", "admin@track.edu.co", "clave1234")
    headers = {"Authorization": f"Bearer {_login(client, 'admin@track.edu.co', 'clave1234')}"}
    route_id = client.post("/api/v1/routes", json={"name": "Ruta track"}, headers=headers).json()["id"]
    for index, (name, lon, lat) in enumerate(STOPS, start=1):
        resp = client.post(
            f"/api/v1/routes/{route_id}/stops",
            json={"name": name, "order_index": index, "location": {"lon": lon, "lat": lat}},
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
    trip = client.post(
        "/api/v1/trips", json={"route_id": route_id, "date": dt.date.today().isoformat()}, headers=headers
    ).json()
    return headers, trip


def _add_positions(db_session, trip, points):
    start = dt.datetime(2026, 10, 1, 6, 30, tzinfo=dt.timezone.utc)
    for i, (lon, lat, speed) in enumerate(points):
        db_session.add(
            GPSPosition(
                tenant_id=trip["tenant_id"],
                trip_id=trip["id"],
                geom=f"SRID=4326;POINT({lon} {lat})",
                speed_kmh=speed,
                recorded_at=start + dt.timedelta(seconds=30 * i),
            )
        )
    db_session.commit()


def test_track_compares_real_path_with_planned(client, db_session):
    headers, trip = _setup(client)
    # 5 puntos sobre la ruta y 1 desviado ~550 m al norte (5 de 6 en ruta).
    _add_positions(
        db_session,
        trip,
        [
            (-73.1300, 7.1100, 20.0),
            (-73.1260, 7.1100, 30.0),
            (-73.1220, 7.1150, 35.0),
            (-73.1180, 7.1100, 28.0),
            (-73.1140, 7.1100, 25.0),
            (-73.1100, 7.1100, 10.0),
        ],
    )

    resp = client.get(f"/api/v1/trips/{trip['id']}/track", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["point_count"] == 6
    assert len(body["coordinates"]) == 6
    assert body["coordinates"][0] == [-73.13, 7.11]
    assert body["duration_minutes"] == 2.5
    assert body["max_speed_kmh"] == 35.0
    assert body["off_route_pct"] == round(1 / 6 * 100, 1)
    assert body["planned"]["source"] == "straight_line"
    # El desvío alarga el recorrido real frente a los ~2.2 km planeados.
    assert body["distance_m"] > body["planned"]["distance_m"]


def test_track_ignores_gps_jumps_in_distance(client, db_session):
    headers, trip = _setup(client)
    # El tercer punto "salta" ~11 km en 30 s: error de GPS, no se suma.
    _add_positions(
        db_session,
        trip,
        [(-73.1300, 7.1100, 20.0), (-73.1290, 7.1100, 20.0), (-73.1290, 7.2100, 20.0)],
    )
    body = client.get(f"/api/v1/trips/{trip['id']}/track", headers=headers).json()
    assert body["distance_m"] < 200


def test_track_without_positions_and_parent_forbidden(client):
    headers, trip = _setup(client)
    body = client.get(f"/api/v1/trips/{trip['id']}/track", headers=headers).json()
    assert body["point_count"] == 0
    assert body["coordinates"] == []
    assert body["off_route_pct"] is None
    assert body["duration_minutes"] is None

    client.post(
        "/api/v1/users",
        json={"email": "padre@track.edu.co", "password": "clave1234", "full_name": "Padre", "role": "PADRE"},
        headers=headers,
    )
    parent_headers = {"Authorization": f"Bearer {_login(client, 'padre@track.edu.co', 'clave1234')}"}
    assert client.get(f"/api/v1/trips/{trip['id']}/track", headers=parent_headers).status_code == 403
