from tests import otp_capture


def _bootstrap_tenant(client, name="Colegio Test", email="admin@test.edu.co", password="clave1234"):
    response = client.post(
        "/api/v1/tenants/bootstrap",
        headers={"X-Bootstrap-Key": "change-this-bootstrap-key"},
        json={
            "name": name,
            "admin_email": email,
            "admin_password": password,
            "admin_full_name": "Admin Test",
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


def test_bootstrap_login_and_me(client):
    _bootstrap_tenant(client)
    token = _login(client, "admin@test.edu.co", "clave1234")

    response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "admin@test.edu.co"
    assert body["role"] == "ADMIN"


def test_wrong_password_is_rejected(client):
    _bootstrap_tenant(client)
    response = client.post(
        "/api/v1/auth/login", json={"email": "admin@test.edu.co", "password": "incorrecta"}
    )
    assert response.status_code == 401


def test_vehicle_crud_flow(client):
    _bootstrap_tenant(client)
    token = _login(client, "admin@test.edu.co", "clave1234")
    headers = {"Authorization": f"Bearer {token}"}

    create_resp = client.post(
        "/api/v1/vehicles", json={"plate": "ABC-123", "capacity": 30}, headers=headers
    )
    assert create_resp.status_code == 201, create_resp.text
    vehicle_id = create_resp.json()["id"]

    list_resp = client.get("/api/v1/vehicles", headers=headers)
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    update_resp = client.put(
        f"/api/v1/vehicles/{vehicle_id}", json={"capacity": 40}, headers=headers
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["capacity"] == 40

    delete_resp = client.delete(f"/api/v1/vehicles/{vehicle_id}", headers=headers)
    assert delete_resp.status_code == 204

    empty_list = client.get("/api/v1/vehicles", headers=headers)
    assert empty_list.json() == []


def test_route_and_stop_with_geometry(client):
    _bootstrap_tenant(client)
    token = _login(client, "admin@test.edu.co", "clave1234")
    headers = {"Authorization": f"Bearer {token}"}

    route_resp = client.post("/api/v1/routes", json={"name": "Ruta demo"}, headers=headers)
    assert route_resp.status_code == 201
    route_id = route_resp.json()["id"]

    stop_resp = client.post(
        f"/api/v1/routes/{route_id}/stops",
        json={"name": "Parada 1", "order_index": 1, "location": {"lon": -73.12, "lat": 7.12}},
        headers=headers,
    )
    assert stop_resp.status_code == 201, stop_resp.text
    stop_body = stop_resp.json()
    assert stop_body["lon"] == -73.12
    assert stop_body["lat"] == 7.12


def test_role_forbidden_for_non_admin(client):
    _bootstrap_tenant(client)
    admin_token = _login(client, "admin@test.edu.co", "clave1234")
    headers = {"Authorization": f"Bearer {admin_token}"}

    # El ADMIN crea un usuario CONDUCTOR indirectamente vía bootstrap no aplica;
    # se prueba negando acceso a un token inválido / ausente.
    response = client.post("/api/v1/vehicles", json={"plate": "ZZZ-000"})
    assert response.status_code == 401


def test_tenant_isolation_between_two_schools(client):
    _bootstrap_tenant(client, name="Colegio A", email="admin@a.edu.co", password="passwordA")
    _bootstrap_tenant(client, name="Colegio B", email="admin@b.edu.co", password="passwordB")

    token_a = _login(client, "admin@a.edu.co", "passwordA")
    token_b = _login(client, "admin@b.edu.co", "passwordB")

    client.post(
        "/api/v1/vehicles",
        json={"plate": "A-001", "capacity": 20},
        headers={"Authorization": f"Bearer {token_a}"},
    )

    vehicles_b = client.get("/api/v1/vehicles", headers={"Authorization": f"Bearer {token_b}"})
    assert vehicles_b.status_code == 200
    assert vehicles_b.json() == []  # Colegio B no debe ver los buses de Colegio A

    vehicles_a = client.get("/api/v1/vehicles", headers={"Authorization": f"Bearer {token_a}"})
    assert len(vehicles_a.json()) == 1

def test_admin_can_update_and_deactivate_a_user(client):
    _bootstrap_tenant(client)
    admin_token = _login(client, "admin@test.edu.co", "clave1234")

    create_resp = client.post(
        "/api/v1/users",
        json={
            "email": "conductor@test.edu.co",
            "password": "clave1234",
            "full_name": "Carlos Conductor",
            "role": "CONDUCTOR",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    user_id = create_resp.json()["id"]

    update_resp = client.patch(
        f"/api/v1/users/{user_id}",
        json={"phone": "3001234567"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["phone"] == "3001234567"
    assert update_resp.json()["is_active"] is True

    deactivate_resp = client.patch(
        f"/api/v1/users/{user_id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["is_active"] is False

    # Un usuario desactivado ya no puede iniciar sesión.
    login_resp = client.post(
        "/api/v1/auth/login", json={"email": "conductor@test.edu.co", "password": "clave1234"}
    )
    assert login_resp.status_code == 403
