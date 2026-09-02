"""
Pruebas de las dos piezas nuevas para la app móvil (rediseño a pedido del
usuario): que un MONITOR pueda reportar una incidencia a mano (alumno
ausente / problema durante el viaje) desde `POST /alerts`, y que un PADRE
solo vea a sus propios hijos en `GET /students` (antes veía el listado
completo del colegio, lo cual exponía datos de estudiantes ajenos).
"""

from tests.test_trips_and_ws import _bootstrap_tenant, _login


def _create_user(client, admin_headers, email, role, full_name):
    resp = client.post(
        "/api/v1/users",
        json={"email": email, "password": "clave1234", "full_name": full_name, "role": role},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _create_student(client, admin_headers, full_name, parent_id=None):
    resp = client.post(
        "/api/v1/students",
        json={"full_name": full_name, "parent_id": parent_id},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_monitor_can_report_a_manual_incident_for_a_student(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"}

    parent_id = _create_user(client, admin_headers, "papa@correo.com", "PADRE", "Papá de Juan")
    student_id = _create_student(client, admin_headers, "Juan Pérez", parent_id=parent_id)
    monitor_id = _create_user(client, admin_headers, "monitor@escuela.edu.co", "MONITOR", "Marta Monitora")
    assert monitor_id
    monitor_token = _login(client, "monitor@escuela.edu.co", "clave1234")
    monitor_headers = {"Authorization": f"Bearer {monitor_token}"}

    resp = client.post(
        "/api/v1/alerts",
        json={"student_id": student_id, "description": "Ausencia reportada: Juan Pérez"},
        headers=monitor_headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["student_id"] == student_id
    assert body["type"] == "INCIDENT"
    assert body["severity"] == "LOW"
    assert body["resolved_at"] is None

    # La incidencia debe aparecer en el listado del colegio.
    listed = client.get("/api/v1/alerts", headers=monitor_headers)
    assert listed.status_code == 200
    assert any(a["id"] == body["id"] for a in listed.json())


def test_padre_cannot_report_incidents(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"}
    _create_user(client, admin_headers, "papa@correo.com", "PADRE", "Papá de Juan")
    padre_token = _login(client, "papa@correo.com", "clave1234")

    resp = client.post(
        "/api/v1/alerts",
        json={"description": "intento no autorizado"},
        headers={"Authorization": f"Bearer {padre_token}"},
    )
    assert resp.status_code == 403


def test_padre_only_sees_their_own_children(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"}

    parent_a = _create_user(client, admin_headers, "papa.a@correo.com", "PADRE", "Papá A")
    parent_b = _create_user(client, admin_headers, "papa.b@correo.com", "PADRE", "Papá B")
    _create_student(client, admin_headers, "Hijo de A", parent_id=parent_a)
    _create_student(client, admin_headers, "Hijo de B", parent_id=parent_b)

    padre_a_token = _login(client, "papa.a@correo.com", "clave1234")
    resp = client.get("/api/v1/students", headers={"Authorization": f"Bearer {padre_a_token}"})
    assert resp.status_code == 200
    names = [s["full_name"] for s in resp.json()]
    assert names == ["Hijo de A"]


def test_admin_still_sees_every_student_in_the_school(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"}

    parent_a = _create_user(client, admin_headers, "papa.a@correo.com", "PADRE", "Papá A")
    parent_b = _create_user(client, admin_headers, "papa.b@correo.com", "PADRE", "Papá B")
    _create_student(client, admin_headers, "Hijo de A", parent_id=parent_a)
    _create_student(client, admin_headers, "Hijo de B", parent_id=parent_b)

    resp = client.get("/api/v1/students", headers=admin_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 2
