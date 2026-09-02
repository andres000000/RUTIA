"""Pruebas del endpoint nuevo `DELETE /api/v1/trips/{id}` (cancelar un viaje
programado) -- lo usa la pantalla nueva de "Viajes" del panel admin para que
el ADMIN pueda borrar un viaje que programó por error, siempre y cuando el
conductor todavía no lo haya iniciado."""

from tests.test_trips_and_ws import _bootstrap_tenant, _login, _setup_route_with_driver


def test_admin_can_cancel_a_scheduled_trip(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"}
    _conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    delete_resp = client.delete(f"/api/v1/trips/{trip_id}", headers=admin_headers)
    assert delete_resp.status_code == 204, delete_resp.text

    trips_resp = client.get("/api/v1/trips", headers=admin_headers)
    assert trip_id not in [t["id"] for t in trips_resp.json()]


def test_cannot_cancel_a_trip_already_in_progress(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"}
    conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    start_resp = client.post(
        f"/api/v1/trips/{trip_id}/start", headers={"Authorization": f"Bearer {conductor_token}"}
    )
    assert start_resp.status_code == 200, start_resp.text

    delete_resp = client.delete(f"/api/v1/trips/{trip_id}", headers=admin_headers)
    assert delete_resp.status_code == 400, delete_resp.text


def test_conductor_cannot_cancel_a_trip(client):
    _bootstrap_tenant(client, "Colegio Nijepra", "admin@nijepra.edu.co", "clave1234")
    admin_headers = {"Authorization": f"Bearer {_login(client, 'admin@nijepra.edu.co', 'clave1234')}"}
    conductor_token, trip_id = _setup_route_with_driver(client, admin_headers)

    delete_resp = client.delete(
        f"/api/v1/trips/{trip_id}", headers={"Authorization": f"Bearer {conductor_token}"}
    )
    assert delete_resp.status_code == 403, delete_resp.text
