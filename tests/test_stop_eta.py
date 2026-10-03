"""ETA por parada (Fase 3): detección de llegadas, entrenamiento y endpoint."""

import datetime as dt

import pytest

from app.ml import predict, stop_eta
from app.models.gps_position import GPSPosition
from app.models.trip import Trip, TripStatus
from tests.test_trips_and_ws import _bootstrap_tenant, _login

# Ruta de oeste a este con 4 paradas separadas ~550 m (la última, el colegio).
STOPS = [
    ("Salida", -73.1300, 7.1100),
    ("Casa Ana", -73.1250, 7.1100),
    ("Casa Beto", -73.1200, 7.1100),
    ("Colegio", -73.1150, 7.1100),
]


@pytest.fixture(autouse=True)
def _isolated_model(tmp_path, monkeypatch):
    """Nunca toca el modelo real de app/ml/trained_models/."""
    monkeypatch.setattr(stop_eta, "STOP_ETA_MODEL_PATH", tmp_path / "stop_eta.joblib")
    predict.clear_model_cache()
    yield
    predict.clear_model_cache()


def test_match_arrivals_is_sequential_and_tolerates_skipped_stops():
    stops = [(0.0, 0.0), (0.01, 0.0), (0.02, 0.0)]
    radii = [100.0] * 3
    # Pasa por la 0, se salta la 1 (va por otro lado) y llega a la 2.
    coords = [(0.0, 0.0), (0.005, 0.005), (0.015, 0.005), (0.02, 0.0)]
    assert stop_eta.match_arrivals(stops, radii, coords) == [0, None, 3]
    assert stop_eta.next_stop_index([0, None, 3], 1) == 1
    assert stop_eta.next_stop_index([0, None, 3], 3) == 3


def _setup(client):
    _bootstrap_tenant(client, "Colegio ETA", "admin@eta.edu.co", "clave1234")
    headers = {"Authorization": f"Bearer {_login(client, 'admin@eta.edu.co', 'clave1234')}"}
    route_id = client.post("/api/v1/routes", json={"name": "Ruta ETA"}, headers=headers).json()["id"]
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


def _start_trip_with_positions(db_session, trip_json, points):
    trip = db_session.get(Trip, trip_json["id"])
    trip.status = TripStatus.IN_PROGRESS
    now = dt.datetime.now(dt.timezone.utc)
    trip.start_time = now - dt.timedelta(minutes=5)
    for i, (lon, lat) in enumerate(points):
        db_session.add(
            GPSPosition(
                tenant_id=trip.tenant_id,
                trip_id=trip.id,
                geom=f"SRID=4326;POINT({lon} {lat})",
                speed_kmh=25.0,
                recorded_at=now - dt.timedelta(seconds=15 * (len(points) - 1 - i)),
            )
        )
    db_session.commit()


def test_stop_etas_without_model_use_heuristic(client, db_session):
    headers, trip = _setup(client)

    # Sin GPS todavía: no hay estimación, pero sí la lista de paradas.
    body = client.get(f"/api/v1/trips/{trip['id']}/stop-etas", headers=headers).json()
    assert body["available"] is False
    assert body["message"]
    assert len(body["stops"]) == 4

    # El bus salió, ya pasó por "Casa Ana" y va a mitad de camino a "Casa Beto".
    _start_trip_with_positions(
        db_session, trip, [(-73.1300, 7.1100), (-73.1275, 7.1100), (-73.1250, 7.1100), (-73.1225, 7.1100)]
    )
    body = client.get(f"/api/v1/trips/{trip['id']}/stop-etas", headers=headers).json()
    assert body["available"] is True
    assert body["source"] == "heuristic"
    statuses = [s["status"] for s in body["stops"]]
    assert statuses == ["passed", "passed", "upcoming", "upcoming"]
    beto, school = body["stops"][2], body["stops"][3]
    assert beto["stops_before"] == 0 and school["stops_before"] == 1
    assert 0 < beto["eta_minutes"] < school["eta_minutes"]
    assert school["remaining_distance_m"] > beto["remaining_distance_m"]


def test_trained_model_beats_baseline_and_is_used(client, db_session):
    headers, trip = _setup(client)
    # Sin viajes grabados, el entrenamiento se apoya en viajes simulados
    # sobre las paradas actuales.
    samples = stop_eta.train_stop_eta_model(db_session)
    assert samples > 50
    bundle = stop_eta.current_bundle()
    assert bundle["simulated_trips"] > 0
    assert bundle["metrics"]["mae_min"] < bundle["metrics"]["baseline_mae_min"]
    assert {f["feature"] for f in bundle["feature_importance"]} == set(stop_eta.FEATURE_NAMES)

    _start_trip_with_positions(db_session, trip, [(-73.1300, 7.1100), (-73.1280, 7.1100)])
    body = client.get(f"/api/v1/trips/{trip['id']}/stop-etas", headers=headers).json()
    assert body["source"] == "model"
    etas = [s["eta_minutes"] for s in body["stops"] if s["status"] == "upcoming"]
    assert len(etas) == 3
    assert etas == sorted(etas)  # nunca una parada posterior "llega" antes

    summary = client.get("/api/v1/ml/summary", headers=headers).json()["stop_eta_model"]
    assert summary["available"] is True
    assert summary["metrics"]["validation"].startswith("Validación cruzada")


def test_parent_can_read_stop_etas(client):
    headers, trip = _setup(client)
    client.post(
        "/api/v1/users",
        json={"email": "padre@eta.edu.co", "password": "clave1234", "full_name": "Padre", "role": "PADRE"},
        headers=headers,
    )
    parent = {"Authorization": f"Bearer {_login(client, 'padre@eta.edu.co', 'clave1234')}"}
    assert client.get(f"/api/v1/trips/{trip['id']}/stop-etas", headers=parent).status_code == 200
