"""
Entrena los dos modelos del Objetivo 4 y los guarda en disco:

(Los dos primeros; el tercero, ETA por parada, vive en `stop_eta.py`.)

1. **Modelo de retrasos** (regresión): predice cuántos minutos debería durar
   un viaje de una ruta dada, según el día de la semana y la hora de salida.
   Se usa en `GET /api/v1/trips/{id}/eta`.
2. **Modelo de anomalías** (Isolation Forest, no supervisado): aprende cómo
   es una velocidad "normal" en las rutas del colegio a partir del histórico,
   para detectar velocidades fuera de patrón en tiempo real. Se usa en el
   WebSocket de GPS (`app/ws/gps.py`) para generar alertas automáticas.

Uso:
    python -m app.ml.train

Si no hay suficientes viajes históricos todavía (el piloto acaba de empezar),
antes de entrenar genera automáticamente un histórico sintético realista
(ver `generate_history.py`) para que el modelo no arranque en blanco. En
cuanto el piloto real acumule datos propios, esos se usan igual (se mezclan
con los sintéticos) — no hace falta ningún cambio para eso.
"""

import datetime as dt

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest, RandomForestRegressor
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.gps_position import GPSPosition
from app.models.tenant import Tenant
from app.models.trip import Trip, TripStatus
from app.ml import generate_history
from app.ml.stop_eta import train_stop_eta_model
from app.ml.paths import ANOMALY_MODEL_PATH, DELAY_MODEL_PATH

# Si un colegio tiene menos viajes completados que esto, se le rellena el
# histórico con datos sintéticos antes de entrenar.
MIN_TRIPS_BEFORE_BACKFILL = 20


def _completed_trips(db: Session) -> list[Trip]:
    return (
        db.query(Trip)
        .filter(Trip.status == TripStatus.COMPLETED, Trip.start_time.isnot(None), Trip.end_time.isnot(None))
        .all()
    )


def _ensure_enough_history(db: Session) -> None:
    for tenant in db.query(Tenant).all():
        count = (
            db.query(Trip)
            .filter(
                Trip.tenant_id == tenant.id,
                Trip.status == TripStatus.COMPLETED,
                Trip.start_time.isnot(None),
            )
            .count()
        )
        if count < MIN_TRIPS_BEFORE_BACKFILL:
            created = generate_history.generate_history_for_tenant(db, tenant.id)
            print(f"  Colegio '{tenant.name}': {count} viajes históricos, se generaron {created} sintéticos.")
        else:
            print(f"  Colegio '{tenant.name}': {count} viajes históricos, suficiente para entrenar.")


def train_delay_model(db: Session) -> int:
    trips = _completed_trips(db)
    if len(trips) < 5:
        print("No hay suficientes viajes completados para entrenar el modelo de retrasos.")
        return 0

    X = []
    y = []
    for trip in trips:
        duration_minutes = (trip.end_time - trip.start_time).total_seconds() / 60
        if duration_minutes <= 0 or duration_minutes > 180:
            continue  # descarta datos corruptos/atípicos que no son un "viaje normal"
        X.append([trip.start_time.weekday(), trip.start_time.hour])
        y.append(duration_minutes)

    model = RandomForestRegressor(n_estimators=150, max_depth=4, random_state=42)
    model.fit(np.array(X), np.array(y))
    joblib.dump(model, DELAY_MODEL_PATH)
    print(f"Modelo de retrasos entrenado con {len(X)} viajes -> {DELAY_MODEL_PATH}")
    return len(X)


def train_anomaly_model(db: Session) -> int:
    speeds = [
        row[0]
        for row in db.query(GPSPosition.speed_kmh).filter(GPSPosition.speed_kmh.isnot(None)).all()
    ]
    if len(speeds) < 30:
        print("No hay suficientes posiciones GPS para entrenar el modelo de anomalías.")
        return 0

    X = np.array(speeds).reshape(-1, 1)
    # contamination=0.06: coincide a propósito con el porcentaje de días con
    # anomalía inyectada en generate_history.py, para que el modelo aprenda a
    # aislar ese mismo porcentaje de puntos como "raros".
    model = IsolationForest(n_estimators=200, contamination=0.06, random_state=42)
    model.fit(X)
    joblib.dump(model, ANOMALY_MODEL_PATH)
    print(f"Modelo de anomalías entrenado con {len(speeds)} posiciones GPS -> {ANOMALY_MODEL_PATH}")
    return len(speeds)


def main() -> None:
    db = SessionLocal()
    try:
        print("Revisando histórico disponible por colegio...")
        _ensure_enough_history(db)
        print()
        print("Entrenando modelo de predicción de retrasos...")
        train_delay_model(db)
        print()
        print("Entrenando modelo de detección de anomalías...")
        train_anomaly_model(db)
        print()
        print("Entrenando modelo de ETA por parada...")
        train_stop_eta_model(db)
        print()
        print("Listo. Los modelos quedaron guardados en app/ml/trained_models/.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
