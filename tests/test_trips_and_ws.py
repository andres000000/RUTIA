import datetime as dt

from tests import otp_capture


def _bootstrap_tenant(client, name, email, password):
    response = client.post(
        "/api/v1/tenants/bootstrap",
        headers={"X-Bootstrap-Key": "change-this-bootstrap-key"},
        json={
            "name": name,
            "admin_email": email,
            "admin_password": password,
            "admin_full_name": "Admin " + name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client, email, password):
    """Inicia sesión y devuelve el access_token, completando el 2FA por
    correo automáticamente si la cuenta es ADMIN (ver test_auth_security.py
    para las pruebas que sí verifican ese flujo a fondo)."""
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    body = response.json()
    if body.get("requires_verification"):
        code = otp_capture.last_code_for(email)
        verify_resp = client.post("/api/v1/auth/verify-login", json={"email": email, "code": code})
        assert verify_resp.status_code == 200, verify_resp.text
        return verify_resp.json()["access_token"]
    return body["access_token"]


def _setup_route_with_driver(client, admin_headers):
    """Crea conductor + bus + ruta + viaje, y devuelve (conductor_token, trip_id)."""
    conductor_resp = client.post(
        "/api/v1/users",
        json={
            "email": "conductor@escuela.edu.co",
            "password": "clave1234",
            "full_name": "Carlos Conductor",
            "role": "CONDUCTOR",
        },
        headers=admin_headers,
    )
    assert conductor_resp.status_code == 201, conductor_resp.text
    conductor_id = conductor_resp.json()["id"]

    vehicle_resp = client.post(
        "/api/v1/vehicles",
        json={"plate": "RUT-001", "capacity": 20, "driver_id": conductor_id},
        headers=admin_headers,
    )
    assert vehicle_resp.status_code == 201, vehicle_resp.text
    vehicle_id = vehicle_resp.json()["id"]

    route_resp = client.post(
        "/api/v1/routes",
        json={"name": "Ruta 1", "vehicle_id": vehicle_id},
        headers=admin_headers,
    )
    assert route_resp.status_code == 201, route_resp.text
    route_id = route_resp.json()["id"]

    trip_resp = client.post(
        "/api/v1/trips",
        json={"route_id": route_id, "date": dt.date.today().isoformat()},
        headers=admin_headers,
    )
    assert trip_resp.status_code == 201, trip_resp.text
    trip_id = trip_resp.json()["id"]

    conductor_token = _login(client, "conductor@escuela.edu.co", "clave1234")
    return conductor_token, trip_id


def test_admin_can_create_conductor_and_it_can_login(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {
        "Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"
    }
    conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)
    assert conductor_token
    assert trip_id


def test_ws_driver_position_is_persisted_and_broadcast(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_token = _login(client, "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    with client.websocket_connect(f"/ws/trips/{trip_id}?token={admin_token}") as watcher_ws:
        with client.websocket_connect(f"/ws/trips/{trip_id}?token={conductor_token}") as driver_ws:
            driver_ws.send_json({"lat": 7.1193, "lon": -73.1198, "speed_kmh": 32.5})
            message = watcher_ws.receive_json()

    assert message["trip_id"] == trip_id
    assert message["lat"] == 7.1193
    assert message["lon"] == -73.1198
    assert message["speed_kmh"] == 32.5

    positions_resp = client.get(f"/api/v1/trips/{trip_id}/positions", headers=admin_headers)
    assert positions_resp.status_code == 200
    positions = positions_resp.json()
    assert len(positions) == 1
    assert positions[0]["lat"] == 7.1193
    assert positions[0]["lon"] == -73.1198

    trip_resp = client.get("/api/v1/trips", headers=admin_headers)
    trip = next(t for t in trip_resp.json() if t["id"] == trip_id)
    assert trip["status"] == "IN_PROGRESS"  # se actualizó solo al llegar el primer punto GPS


def test_ws_non_driver_cannot_publish_positions(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_token = _login(client, "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    _conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    padre_resp = client.post(
        "/api/v1/users",
        json={
            "email": "padre@escuela.edu.co",
            "password": "clave1234",
            "full_name": "Pedro Padre",
            "role": "PADRE",
        },
        headers=admin_headers,
    )
    assert padre_resp.status_code == 201
    padre_token = _login(client, "padre@escuela.edu.co", "clave1234")

    with client.websocket_connect(f"/ws/trips/{trip_id}?token={padre_token}") as padre_ws:
        # Un PADRE solo puede escuchar: si intenta "publicar" una posición, se ignora.
        padre_ws.send_json({"lat": 0.0, "lon": 0.0})

    positions_resp = client.get(f"/api/v1/trips/{trip_id}/positions", headers=admin_headers)
    assert positions_resp.json() == []


def test_ws_rejects_user_from_another_tenant(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_token = _login(client, "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    _conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    _bootstrap_tenant(client, "Otro Colegio", "admin@otro.edu.co", "clave1234")
    otro_admin_token = _login(client, "admin@otro.edu.co", "clave1234")

    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect(f"/ws/trips/{trip_id}?token={otro_admin_token}"):
            raised = False
    except WebSocketDisconnect:
        raised = True
    assert raised, "Un usuario de otro colegio no debería poder conectarse a este viaje"


def test_ws_extreme_speed_creates_and_broadcasts_alert(client):
    """Objetivo 4: una velocidad claramente peligrosa (>80 km/h) debe generar
    una alerta HIGH en tiempo real, no solo quedar como un punto GPS más."""
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_token = _login(client, "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    with client.websocket_connect(f"/ws/trips/{trip_id}?token={admin_token}") as watcher_ws:
        with client.websocket_connect(f"/ws/trips/{trip_id}?token={conductor_token}") as driver_ws:
            driver_ws.send_json({"lat": 7.1193, "lon": -73.1198, "speed_kmh": 95.0})
            position_message = watcher_ws.receive_json()
            alert_message = watcher_ws.receive_json()

    assert position_message["type"] == "position"
    assert alert_message["type"] == "alert"
    assert alert_message["trip_id"] == trip_id
    assert alert_message["alert_type"] == "ANOMALY"
    assert alert_message["severity"] == "HIGH"

    alerts_resp = client.get("/api/v1/alerts", headers=admin_headers)
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()
    assert len(alerts) == 1
    assert alerts[0]["severity"] == "HIGH"
    assert alerts[0]["resolved_at"] is None

    resolve_resp = client.post(f"/api/v1/alerts/{alerts[0]['id']}/resolve", headers=admin_headers)
    assert resolve_resp.status_code == 200, resolve_resp.text
    assert resolve_resp.json()["resolved_at"] is not None


def test_ws_normal_speed_does_not_create_alert(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_token = _login(client, "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    with client.websocket_connect(f"/ws/trips/{trip_id}?token={admin_token}") as watcher_ws:
        with client.websocket_connect(f"/ws/trips/{trip_id}?token={conductor_token}") as driver_ws:
            driver_ws.send_json({"lat": 7.1193, "lon": -73.1198, "speed_kmh": 27.0})
            position_message = watcher_ws.receive_json()

    assert position_message["type"] == "position"

    alerts_resp = client.get("/api/v1/alerts", headers=admin_headers)
    assert alerts_resp.json() == []
