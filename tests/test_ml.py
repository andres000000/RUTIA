"""
Pruebas automatizadas del Objetivo 4 (módulo de IA): generación de histórico
sintético, entrenamiento de los dos modelos (retrasos y anomalías), e inferencia
(`predict.py`).

Estas pruebas SIEMPRE apuntan los modelos a archivos temporales (via monkeypatch
de `DELAY_MODEL_PATH`/`ANOMALY_MODEL_PATH` tanto en `train` como en `predict`),
nunca a `app/ml/trained_models/`, para no pisar los modelos reales que ya se
entrenaron a mano en este entorno de desarrollo. También se limpia el cache de
`lru_cache` (`predict.clear_model_cache()`) antes y después de cada prueba que
toca modelos, porque si no, una prueba puede dejar en memoria un modelo cargado
desde una ruta temporal que ya no existe cuando corre la siguiente prueba.
"""

import datetime as dt

import pytest

from app.ml import generate_history, predict, train
from app.models.alert import AlertSeverity
from app.models.route import Route, Stop
from app.models.tenant import Tenant
from app.models.vehicle import Vehicle


def _make_route_with_stops(db_session, tenant_id: int) -> Route:
    """Crea un vehículo + una ruta + 2 paradas, lo mínimo que necesita
    `generate_history.generate_history_for_route` para poder simular viajes."""
    vehicle = Vehicle(tenant_id=tenant_id, plate="RUT-100", capacity=20)
    db_session.add(vehicle)
    db_session.flush()

    route = Route(
        tenant_id=tenant_id,
        name="Ruta de prueba",
        vehicle_id=vehicle.id,
        scheduled_start_time=dt.time(6, 30),
    )
    db_session.add(route)
    db_session.flush()

    stop_a = Stop(
        tenant_id=tenant_id,
        route_id=route.id,
        name="Parada 1",
        order_index=0,
        geom="SRID=4326;POINT(-73.120 7.120)",
    )
    stop_b = Stop(
        tenant_id=tenant_id,
        route_id=route.id,
        name="Parada 2",
        order_index=1,
        geom="SRID=4326;POINT(-73.100 7.140)",
    )
    db_session.add_all([stop_a, stop_b])
    db_session.commit()
    db_session.refresh(route)
    return route


@pytest.fixture(autouse=True)
def _reset_model_cache():
    """Evita que el cache en memoria de los modelos "se cuele" de una prueba a
    otra (o hacia/desde el resto de la suite): se limpia antes y después de
    cada prueba de este archivo."""
    predict.clear_model_cache()
    yield
    predict.clear_model_cache()


def test_evaluate_speed_anomaly_rule_based_thresholds(tmp_path, monkeypatch):
    # Sin modelo entrenado (ruta apunta a un archivo que no existe), la función
    # debe funcionar solo con las reglas fijas de dominio.
    monkeypatch.setattr(predict, "ANOMALY_MODEL_PATH", tmp_path / "no_existe.joblib")
    predict.clear_model_cache()

    assert predict.evaluate_speed_anomaly(None) is None
    assert predict.evaluate_speed_anomaly(25.0) is None  # velocidad normal de ruta escolar

    medium = predict.evaluate_speed_anomaly(65.0)  # > 60 km/h, dentro del rango "medio"
    assert medium is not None
    assert medium.severity == AlertSeverity.MEDIUM

    high = predict.evaluate_speed_anomaly(95.0)  # > 80 km/h: siempre HIGH, con o sin modelo
    assert high is not None
    assert high.severity == AlertSeverity.HIGH


def test_predict_duration_falls_back_to_default_without_model_or_history(db_session, tmp_path, monkeypatch):
    monkeypatch.setattr(predict, "DELAY_MODEL_PATH", tmp_path / "no_existe.joblib")
    predict.clear_model_cache()

    tenant = Tenant(name="Colegio de Prueba")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    # Sin modelo entrenado y sin ningún viaje histórico para esa ruta: debe
    # devolver el valor por defecto, dejando explícito el origen ("heuristic_default").
    result = predict.predict_trip_duration_minutes(
        db_session, route_id=999999, start_dt=dt.datetime.now(dt.timezone.utc)
    )
    assert result.source == "heuristic_default"
    assert result.predicted_duration_minutes == predict.DEFAULT_DURATION_MINUTES

    # Con viajes históricos completados para la ruta pero sin modelo, debe usar
    # la mediana de esos viajes en vez del valor por defecto genérico.
    route = _make_route_with_stops(db_session, tenant.id)
    created = generate_history.generate_history_for_route(db_session, route, weeks=1, seed=7)
    assert created > 0

    result_with_history = predict.predict_trip_duration_minutes(
        db_session, route_id=route.id, start_dt=dt.datetime.now(dt.timezone.utc)
    )
    assert result_with_history.source == "heuristic_historical"
    assert result_with_history.predicted_duration_minutes > 0


def test_generate_history_and_train_produce_usable_models(db_session, tmp_path, monkeypatch):
    delay_model_path = tmp_path / "delay_model.joblib"
    anomaly_model_path = tmp_path / "anomaly_model.joblib"

    # El entrenamiento y la inferencia leen la ruta del modelo desde su propio
    # módulo (cada uno hizo su propio `from app.ml.paths import ...`), así que
    # hay que parchear las dos referencias, no solo la de `app.ml.paths`.
    monkeypatch.setattr(train, "DELAY_MODEL_PATH", delay_model_path)
    monkeypatch.setattr(train, "ANOMALY_MODEL_PATH", anomaly_model_path)
    monkeypatch.setattr(predict, "DELAY_MODEL_PATH", delay_model_path)
    monkeypatch.setattr(predict, "ANOMALY_MODEL_PATH", anomaly_model_path)
    predict.clear_model_cache()

    tenant = Tenant(name="Colegio de Prueba")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    route = _make_route_with_stops(db_session, tenant.id)

    # 2 semanas de histórico ya bastan para tener suficientes viajes/posiciones
    # con las que entrenar en una prueba (no hace falta usar las 9 semanas de
    # producción, que harían la prueba innecesariamente lenta).
    created = generate_history.generate_history_for_route(db_session, route, weeks=2, seed=123)
    assert created >= 5  # ~10 días hábiles en 2 semanas

    trips_trained = train.train_delay_model(db_session)
    positions_trained = train.train_anomaly_model(db_session)
    assert trips_trained >= 5
    assert positions_trained >= 30
    assert delay_model_path.exists()
    assert anomaly_model_path.exists()

    predict.clear_model_cache()  # para que predict.py cargue los modelos recién entrenados

    prediction = predict.predict_trip_duration_minutes(
        db_session, route_id=route.id, start_dt=dt.datetime.now(dt.timezone.utc)
    )
    assert prediction.source == "model"
    assert prediction.predicted_duration_minutes > 0

    # Una velocidad claramente anómala (inyectada en generate_history.py entre
    # 85-105 km/h) debe seguir marcándose como HIGH gracias a la regla fija,
    # y una velocidad típica de la ruta (~28 km/h) no debe dispararse como
    # anomalía por el modelo recién entrenado con ese mismo histórico.
    assert predict.evaluate_speed_anomaly(95.0).severity == AlertSeverity.HIGH
    assert predict.evaluate_speed_anomaly(28.0) is None


def test_model_summary_is_admin_only_and_describes_both_models(client):
    from app.ml.summary import clear_summary_cache
    from tests.test_auth_and_crud import _bootstrap_tenant, _login

    clear_summary_cache()
    _bootstrap_tenant(client)
    headers = {"Authorization": f"Bearer {_login(client, 'admin@test.edu.co', 'clave1234')}"}

    resp = client.get("/api/v1/ml/summary", headers=headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["delay_model"]["algorithm"].startswith("Random Forest")
    assert body["anomaly_model"]["algorithm"].startswith("Isolation Forest")
    # Sin histórico en la base de pruebas no se inventan métricas.
    assert body["delay_model"]["metrics"] is None
    assert client.get("/api/v1/ml/summary").status_code == 401
