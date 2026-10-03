"""Correos sin distinguir mayúsculas y edición de personal desde el panel."""

from app.core.security import hash_password
from app.models.user import Role, User
from tests.test_trips_and_ws import _bootstrap_tenant, _login


def _admin_headers(client):
    _bootstrap_tenant(client, "Colegio Correos", "admin@correos.edu.co", "clave1234")
    return {"Authorization": f"Bearer {_login(client, 'admin@correos.edu.co', 'clave1234')}"}


def _create_parent(client, headers, email="Padre.Nuevo@Correos.edu.co"):
    resp = client.post(
        "/api/v1/users",
        json={"email": email, "password": "temporal123", "full_name": "Padre Nuevo", "role": "PADRE"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_parent_created_with_capitals_can_login_in_any_case(client):
    headers = _admin_headers(client)
    created = _create_parent(client, headers)
    assert created["email"] == "padre.nuevo@correos.edu.co"

    for typed in ("padre.nuevo@correos.edu.co", "Padre.Nuevo@Correos.edu.co", "PADRE.NUEVO@CORREOS.EDU.CO"):
        resp = client.post("/api/v1/auth/login", json={"email": typed, "password": "temporal123"})
        assert resp.status_code == 200, (typed, resp.text)
        assert resp.json()["access_token"]


def test_duplicate_email_rejected_regardless_of_case(client):
    headers = _admin_headers(client)
    _create_parent(client, headers)
    resp = client.post(
        "/api/v1/users",
        json={"email": "PADRE.nuevo@correos.edu.co", "password": "temporal123", "full_name": "Otro", "role": "PADRE"},
        headers=headers,
    )
    assert resp.status_code == 409


def test_legacy_mixed_case_account_still_logs_in(client, db_session):
    """Cuentas guardadas con mayúsculas antes de normalizar (ya hay en producción)."""
    headers = _admin_headers(client)
    tenant_id = client.get("/api/v1/auth/me", headers=headers).json()["tenant_id"]
    db_session.add(
        User(
            tenant_id=tenant_id,
            email="Viejo.Padre@correos.edu.co",
            hashed_password=hash_password("temporal123"),
            full_name="Padre Viejo",
            role=Role.PADRE,
        )
    )
    db_session.commit()
    resp = client.post("/api/v1/auth/login", json={"email": "viejo.padre@correos.edu.co", "password": "temporal123"})
    assert resp.status_code == 200, resp.text


def test_admin_edits_name_phone_email_and_password(client):
    headers = _admin_headers(client)
    user = _create_parent(client, headers)

    resp = client.patch(
        f"/api/v1/users/{user['id']}",
        json={
            "full_name": "Padre Editado",
            "phone": "3001234567",
            "email": "Corregido@Correos.edu.co",
            "password": "nuevaClave99",
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["full_name"] == "Padre Editado"
    assert body["phone"] == "3001234567"
    assert body["email"] == "corregido@correos.edu.co"

    old = client.post("/api/v1/auth/login", json={"email": "corregido@correos.edu.co", "password": "temporal123"})
    assert old.status_code == 401
    new = client.post("/api/v1/auth/login", json={"email": "corregido@correos.edu.co", "password": "nuevaClave99"})
    assert new.status_code == 200, new.text


def test_edit_email_conflict_and_short_password(client):
    headers = _admin_headers(client)
    user = _create_parent(client, headers)
    resp = client.patch(f"/api/v1/users/{user['id']}", json={"email": "ADMIN@correos.edu.co"}, headers=headers)
    assert resp.status_code == 409
    resp = client.patch(f"/api/v1/users/{user['id']}", json={"password": "corta"}, headers=headers)
    assert resp.status_code == 422
