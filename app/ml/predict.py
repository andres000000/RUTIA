"""
Funciones de inferencia que usa el resto del backend en caliente:

- `predict_trip_duration_minutes`: la usa `GET /api/v1/trips/{id}/eta`.
- `evaluate_speed_anomaly`: la usa el WebSocket de GPS (`app/ws/gps.py`) cada
  vez que llega una posición del conductor, para decidir si crear una alerta.

Diseño deliberado: si el modelo entrenado no existe todavía (por ejemplo,
nadie ha corrido `python -m app.ml.train`) o si algo falla al cargarlo, estas
funciones NUNCA lanzan una excepción que tumbe una petición o la conexión del
WebSocket — devuelven un resultado con un método de respaldo (una regla fija
o el promedio histórico), y lo dejan explícito en el resultado (`source`)
para que quede claro en la demo cuál de los dos está respondiendo.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from functools import lru_cache

import joblib
from sqlalchemy.orm import Session

from app.models.alert import AlertSeverity
from app.models.trip import Trip, TripStatus
from app.ml.paths import ANOMALY_MODEL_PATH, DELAY_MODEL_PATH

DEFAULT_DURATION_MINUTES = 25.0
HIGH_SPEED_THRESHOLD_KMH = 80.0
MEDIUM_SPEED_THRESHOLD_KMH = 60.0


@lru_cache(maxsize=1)
def _load_delay_model():
    if not DELAY_MODEL_PATH.exists():
        return None
    try:
        return joblib.load(DELAY_MODEL_PATH)
    except Exception:
        return None


@lru_cache(maxsize=1)
def _load_anomaly_model():
    if not ANOMALY_MODEL_PATH.exists():
        return None
    try:
        return joblib.load(ANOMALY_MODEL_PATH)
    except Exception:
        return None


def clear_model_cache() -> None:
    """Se usa en las pruebas automatizadas después de entrenar un modelo nuevo,
    para que `predict.py` no siga usando una versión vieja cacheada en memoria."""
    _load_delay_model.cache_clear()
    _load_anomaly_model.cache_clear()


@dataclass
class DelayPrediction:
    predicted_duration_minutes: float
    source: str  # "model" | "heuristic_historical" | "heuristic_default"


def predict_trip_duration_minutes(db: Session, route_id: int, start_dt: dt.datetime) -> DelayPrediction:
    model = _load_delay_model()
    if model is not None:
        prediction = model.predict([[start_dt.weekday(), start_dt.hour]])[0]
        return DelayPrediction(predicted_duration_minutes=round(float(prediction), 1), source="model")

    # Sin modelo entrenado todavía: usar la mediana histórica de esa ruta si
    # hay alguna, y si no, un valor por defecto razonable.
    durations = [
        (trip.end_time - trip.start_time).total_seconds() / 60
        for trip in db.query(Trip)
        .filter(Trip.route_id == route_id, Trip.status == TripStatus.COMPLETED, Trip.end_time.isnot(None))
        .all()
        if trip.start_time is not None
    ]
    if durations:
        durations.sort()
        median = durations[len(durations) // 2]
        return DelayPrediction(predicted_duration_minutes=round(median, 1), source="heuristic_historical")

    return DelayPrediction(predicted_duration_minutes=DEFAULT_DURATION_MINUTES, source="heuristic_default")


@dataclass
class AnomalyResult:
    severity: AlertSeverity
    description: str


def evaluate_speed_anomaly(speed_kmh: float | None) -> AnomalyResult | None:
    """Devuelve una alerta si la velocidad reportada es anómala, o None si es normal.

    Combina dos señales a propósito, no solo una:
    - Una regla de dominio fija (una velocidad > 80 km/h en una ruta escolar es
      inequívocamente peligrosa, con o sin modelo entrenado).
    - El modelo de Isolation Forest entrenado con el histórico real de esa
      flota, que puede detectar patrones menos obvios que una regla fija no
      capturaría (por ejemplo, una velocidad moderadamente alta para una ruta
      que históricamente siempre va lento).
    """
    if speed_kmh is None:
        return None

    if speed_kmh > HIGH_SPEED_THRESHOLD_KMH:
        return AnomalyResult(
            severity=AlertSeverity.HIGH,
            description=f"Velocidad de {speed_kmh:.0f} km/h muy por encima de lo esperado en una ruta escolar.",
        )

    model = _load_anomaly_model()
    is_model_outlier = False
    if model is not None:
        is_model_outlier = model.predict([[speed_kmh]])[0] == -1

    if speed_kmh > MEDIUM_SPEED_THRESHOLD_KMH or is_model_outlier:
        return AnomalyResult(
            severity=AlertSeverity.MEDIUM,
            description=f"Velocidad de {speed_kmh:.0f} km/h fuera del patrón habitual de esta ruta.",
        )

    return None
