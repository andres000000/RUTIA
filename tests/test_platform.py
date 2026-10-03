"""
Pruebas del panel de operador de plataforma ("súper-admin"): login con la
clave de plataforma, listar todos los colegios, crear un colegio nuevo desde
el panel, y "entrar" a administrar un colegio sin pasar por su login+2FA
propio.
"""

from app.core.config import settings
from tests import otp_capture


def _bootstrap_tenant(client, name, admin_email, admin_password):
    response = client.post(
        "/api/v1/tenants/bootstrap",
        headers={"X-Bootstrap-Key": settings.PLATFORM_BOOTSTRAP_KEY},
        json={
            "name": name,
            "admin_email": admin_email,
            "admin_password": admin_password,
            "admin_full_name": "Admin " + name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _platform_login(client) -> str:
    """Login completo de operador: clave + código de 6 dígitos (2FA)."""
    resp = client.post("/api/v1/platform/login", json={"platform_key": settings.PLATFORM_BOOTSTRAP_KEY})
    assert resp.status_code == 200, resp.text
    challenge_id = resp.json()["challenge_id"]
    verify = client.post(
        "/api/v1/platform/verify-login",
        json={"challenge_id": challenge_id, "code": otp_capture.last_code_for("platform")},
    )
    assert verify.status_code == 200, verify.text
    return verify.json()["access_token"]


def test_platform_login_rejects_wrong_key(client):
    resp = client.post("/api/v1/platform/login", json={"platform_key": "clave-incorrecta"})
    assert resp.status_code == 401


def test_platform_login_accepts_correct_key_and_code(client):
    token = _platform_login(client)
    assert token
    resp = client.get("/api/v1/platform/tenants", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_platform_key_alone_no_longer_gives_a_token(client):
    """La clave sola ya no basta: el primer paso solo envía el código."""
    resp = client.post("/api/v1/platform/login", json={"platform_key": settings.PLATFORM_BOOTSTRAP_KEY})
    body = resp.json()
    assert body["requires_verification"] is True
    assert "access_token" not in body
    assert body["challenge_id"]


def test_platform_wrong_code_and_code_reuse_are_rejected(client):
    resp = client.post("/api/v1/platform/login", json={"platform_key": settings.PLATFORM_BOOTSTRAP_KEY})
    challenge_id = resp.json()["challenge_id"]
    code = otp_capture.last_code_for("platform")
    wrong = "000000" if code != "000000" else "111111"

    bad = client.post("/api/v1/platform/verify-login", json={"challenge_id": challenge_id, "code": wrong})
    assert bad.status_code == 400
    bad_id = client.post("/api/v1/platform/verify-login", json={"challenge_id": "otro", "code": code})
    assert bad_id.status_code == 400

    ok = client.post("/api/v1/platform/verify-login", json={"challenge_id": challenge_id, "code": code})
    assert ok.status_code == 200
    # De un solo uso.
    again = client.post("/api/v1/platform/verify-login", json={"challenge_id": challenge_id, "code": code})
    assert again.status_code == 400


def test_platform_code_dies_after_max_attempts(client):
    resp = client.post("/api/v1/platform/login", json={"platform_key": settings.PLATFORM_BOOTSTRAP_KEY})
    challenge_id = resp.json()["challenge_id"]
    code = otp_capture.last_code_for("platform")
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(settings.AUTH_CODE_MAX_ATTEMPTS):
        client.post("/api/v1/platform/verify-login", json={"challenge_id": challenge_id, "code": wrong})
    late = client.post("/api/v1/platform/verify-login", json={"challenge_id": challenge_id, "code": code})
    assert late.status_code == 400


def test_platform_locks_after_repeated_wrong_keys(client):
    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        assert client.post("/api/v1/platform/login", json={"platform_key": "mala"}).status_code == 401
    assert client.post("/api/v1/platform/login", json={"platform_key": "mala"}).status_code == 423
    # Bloqueado: ni siquiera la clave correcta entra mientras dure el bloqueo.
    resp = client.post("/api/v1/platform/login", json={"platform_key": settings.PLATFORM_BOOTSTRAP_KEY})
    assert resp.status_code == 423


def test_platform_endpoints_reject_a_normal_admin_token(client):
    """Un token de ADMIN normal (o ninguno) no sirve para los endpoints de
    plataforma -- son dos conceptos de sesión completamente separados."""
    _bootstrap_tenant(client, "Colegio Uno", "admin@uno.edu.co", "clave1234")
    login_resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@uno.edu.co", "password": "clave1234"}
    )
    code = otp_capture.last_code_for("admin@uno.edu.co")
    admin_token = client.post(
        "/api/v1/auth/verify-login", json={"email": "admin@uno.edu.co", "code": code}
    ).json()["access_token"]

    resp = client.get("/api/v1/platform/tenants", headers={"Authorization": f"Bearer {admin_token}"})
    assert resp.status_code == 401

    resp_no_token = client.get("/api/v1/platform/tenants")
    assert resp_no_token.status_code == 401


def test_platform_lists_all_tenants_with_counts(client):
    _bootstrap_tenant(client, "Colegio Uno", "admin@uno.edu.co", "clave1234")
    _bootstrap_tenant(client, "Colegio Dos", "admin@dos.edu.co", "clave1234")
    token = _platform_login(client)

    resp = client.get("/api/v1/platform/tenants", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    names = {t["name"] for t in resp.json()}
    assert {"Colegio Uno", "Colegio Dos"}.issubset(names)

    colegio_uno = next(t for t in resp.json() if t["name"] == "Colegio Uno")
    assert colegio_uno["admin_email"] == "admin@uno.edu.co"
    assert colegio_uno["vehicles_count"] == 0
    assert colegio_uno["routes_count"] == 0
    assert colegio_uno["students_count"] == 0


def test_platform_can_create_a_new_tenant(client):
    token = _platform_login(client)
    resp = client.post(
        "/api/v1/platform/tenants",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Colegio Nuevo",
            "admin_email": "admin@nuevo.edu.co",
            "admin_password": "clave1234",
            "admin_full_name": "Admin Nuevo",
        },
    )
    assert resp.status_code == 201, resp.text

    listing = client.get("/api/v1/platform/tenants", headers={"Authorization": f"Bearer {token}"})
    assert any(t["name"] == "Colegio Nuevo" for t in listing.json())


def test_platform_can_enter_a_tenant_without_its_own_2fa(client):
    """Este es el punto central de la funcionalidad: el operador de
    plataforma entra directo al colegio, sin necesitar la contraseña ni el
    código de 2FA del ADMIN de ese colegio."""
    bootstrap = _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    tenant_id = bootstrap["tenant"]["id"]
    token = _platform_login(client)

    resp = client.post(
        f"/api/v1/platform/tenants/{tenant_id}/enter", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tenant"]["name"] == "Colegio Nijepra"
    admin_token = body["access_token"]

    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {admin_token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "ADMIN"
    assert me_resp.json()["tenant_id"] == tenant_id


def test_platform_enter_unknown_tenant_returns_404(client):
    token = _platform_login(client)
    resp = client.post("/api/v1/platform/tenants/999999/enter", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 404
