"""
Pruebas de los mecanismos de seguridad de autenticación agregados a pedido
del usuario: 2FA por correo para ADMIN, bloqueo por intentos fallidos, y
recuperación de contraseña.

El envío real de correo nunca ocurre aquí: `conftest.py` (fixture
`_capture_otp_codes`, autouse=True para toda la suite) intercepta
`app.services.auth_codes.send_otp_email` y guarda cada código en
`tests.otp_capture.SENT_CODES`, de donde `otp_capture.last_code_for(email)`
lo recupera para el resto de cada prueba.
"""

from app.core.config import settings
from tests import otp_capture


def _bootstrap_tenant(client, name, admin_email, admin_password):
    response = client.post(
        "/api/v1/tenants/bootstrap",
        headers={"X-Bootstrap-Key": "change-this-bootstrap-key"},
        json={
            "name": name,
            "admin_email": admin_email,
            "admin_password": admin_password,
            "admin_full_name": "Admin " + name,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_admin_login_requires_email_code_and_verify_login_completes_it(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")

    login_resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "clave1234"}
    )
    assert login_resp.status_code == 200, login_resp.text
    body = login_resp.json()
    assert body["requires_verification"] is True
    assert body["access_token"] is None

    code = otp_capture.last_code_for("admin@nijepra.edu.co")
    assert len(code) == 6 and code.isdigit()

    verify_resp = client.post(
        "/api/v1/auth/verify-login", json={"email": "admin@nijepra.edu.co", "code": code}
    )
    assert verify_resp.status_code == 200, verify_resp.text
    token = verify_resp.json()["access_token"]
    assert token

    me_resp = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["role"] == "ADMIN"

    # El código es de un solo uso: reutilizarlo debe fallar.
    reuse_resp = client.post(
        "/api/v1/auth/verify-login", json={"email": "admin@nijepra.edu.co", "code": code}
    )
    assert reuse_resp.status_code == 400


def test_conductor_login_bypasses_two_factor(client):
    """Regresión importante: la app móvil (PADRE/CONDUCTOR/MONITOR) espera
    recibir el token de una vez, sin pasar por 2FA."""
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    client.post("/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "clave1234"})
    admin_code = otp_capture.last_code_for("admin@nijepra.edu.co")
    admin_token = client.post(
        "/api/v1/auth/verify-login", json={"email": "admin@nijepra.edu.co", "code": admin_code}
    ).json()["access_token"]

    conductor_resp = client.post(
        "/api/v1/users",
        json={
            "email": "conductor@escuela.edu.co",
            "password": "clave1234",
            "full_name": "Carlos Conductor",
            "role": "CONDUCTOR",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert conductor_resp.status_code == 201, conductor_resp.text

    conductor_login = client.post(
        "/api/v1/auth/login", json={"email": "conductor@escuela.edu.co", "password": "clave1234"}
    )
    assert conductor_login.status_code == 200
    body = conductor_login.json()
    assert body["requires_verification"] is False
    assert body["access_token"]  # token directo, como antes de este cambio


def test_account_locks_after_max_failed_attempts(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")

    for _ in range(settings.LOGIN_MAX_FAILED_ATTEMPTS - 1):
        resp = client.post(
            "/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "incorrecta"}
        )
        assert resp.status_code == 401

    # El intento número LOGIN_MAX_FAILED_ATTEMPTS dispara el bloqueo.
    locking_resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "incorrecta"}
    )
    assert locking_resp.status_code == 423

    # Incluso con la contraseña correcta, la cuenta sigue bloqueada.
    correct_password_resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "clave1234"}
    )
    assert correct_password_resp.status_code == 423


def test_verify_login_wrong_code_is_rejected_and_limited(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    client.post("/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "clave1234"})
    real_code = otp_capture.last_code_for("admin@nijepra.edu.co")
    wrong_code = "000000" if real_code != "000000" else "111111"

    for _ in range(settings.AUTH_CODE_MAX_ATTEMPTS):
        resp = client.post(
            "/api/v1/auth/verify-login", json={"email": "admin@nijepra.edu.co", "code": wrong_code}
        )
        assert resp.status_code == 400

    # Tras agotar los intentos, ni siquiera el código correcto ya sirve.
    final_resp = client.post(
        "/api/v1/auth/verify-login", json={"email": "admin@nijepra.edu.co", "code": real_code}
    )
    assert final_resp.status_code == 400


def test_forgot_password_does_not_reveal_whether_email_exists(client):
    known_resp = client.post("/api/v1/auth/forgot-password", json={"email": "no-existe@nadie.com"})
    assert known_resp.status_code == 200

    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    existing_resp = client.post(
        "/api/v1/auth/forgot-password", json={"email": "admin@nijepra.edu.co"}
    )
    assert existing_resp.status_code == 200
    # Mismo mensaje genérico en los dos casos -- no delata si la cuenta existe.
    assert known_resp.json()["message"] == existing_resp.json()["message"]


def test_reset_password_full_flow_changes_password(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")

    client.post("/api/v1/auth/forgot-password", json={"email": "admin@nijepra.edu.co"})
    code = otp_capture.last_code_for("admin@nijepra.edu.co")

    reset_resp = client.post(
        "/api/v1/auth/reset-password",
        json={"email": "admin@nijepra.edu.co", "code": code, "new_password": "clavenueva9"},
    )
    assert reset_resp.status_code == 200, reset_resp.text

    # La contraseña vieja ya no funciona.
    old_password_resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "clave1234"}
    )
    assert old_password_resp.status_code == 401

    # La nueva sí (llega hasta pedir el 2FA, porque sigue siendo ADMIN).
    new_password_resp = client.post(
        "/api/v1/auth/login", json={"email": "admin@nijepra.edu.co", "password": "clavenueva9"}
    )
    assert new_password_resp.status_code == 200
    assert new_password_resp.json()["requires_verification"] is True
