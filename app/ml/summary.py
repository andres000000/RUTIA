"""
Ficha técnica de los modelos del Objetivo 4, para la sección "Modelo" y el
reporte PDF del panel admin.

Las métricas se calculan en el momento sobre los datos actuales (no se
guardan al entrenar), con dos criterios de honestidad:

- **Modelo de duración (regresión):** validación cruzada de 5 particiones
  (cada viaje se predice con un modelo que NO lo vio al entrenar) y se compara
  contra una línea base ingenua (predecir siempre el promedio). Un R² o un MAE
  sin línea base no dice si el modelo aporta algo.
- **Modelo de anomalías (Isolation Forest, no supervisado):** no hay etiquetas
  reales de "esto fue una anomalía", así que precisión/recall/F1 se miden
  contra la regla de seguridad (> 80 km/h) como referencia, y se dice
  explícitamente en el resultado (`reference`).

El cálculo tarda alrededor de un segundo, así que se guarda en caché unos
minutos para que abrir varias veces la página no lo repita.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import f1_score, mean_absolute_error, mean_squared_error, precision_score, r2_score, recall_score
from sklearn.model_selection import KFold, cross_val_predict
from sqlalchemy.orm import Session

from app.ml.paths import ANOMALY_MODEL_PATH, DELAY_MODEL_PATH
from app.ml.predict import HIGH_SPEED_THRESHOLD_KMH
from app.models.gps_position import GPSPosition
from app.models.trip import Trip, TripStatus

CACHE_SECONDS = 600
_cache: dict[str, Any] = {"at": 0.0, "value": None}

WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _trained_at(path) -> str | None:
    if not path.exists():
        return None
    return dt.datetime.fromtimestamp(path.stat().st_mtime, tz=dt.timezone.utc).isoformat()


def _load(path):
    try:
        return joblib.load(path) if path.exists() else None
    except Exception:
        return None


def _delay_section(db: Session) -> dict[str, Any]:
    model = _load(DELAY_MODEL_PATH)
    rows = (
        db.query(Trip.start_time, Trip.end_time)
        .filter(Trip.status == TripStatus.COMPLETED, Trip.start_time.isnot(None), Trip.end_time.isnot(None))
        .all()
    )
    X, y = [], []
    for start, end in rows:
        minutes = (end - start).total_seconds() / 60
        if 0 < minutes <= 180:  # mismo filtro que app/ml/train.py
            X.append([start.weekday(), start.hour])
            y.append(minutes)

    section: dict[str, Any] = {
        "available": model is not None,
        "name": "Predicción de duración del viaje (ETA)",
        "algorithm": "Random Forest Regressor (scikit-learn)",
        "task": "Regresión supervisada",
        "target": "Duración total del viaje en minutos",
        "features": ["Día de la semana de salida", "Hora de salida"],
        "params": (
            {"n_estimators": model.n_estimators, "max_depth": model.max_depth} if model is not None else {}
        ),
        "training_samples": len(X),
        "trained_at": _trained_at(DELAY_MODEL_PATH),
        "metrics": None,
        "duration_by_weekday": [],
    }

    if len(X) >= 10:
        X_arr, y_arr = np.array(X), np.array(y)
        folds = KFold(n_splits=5, shuffle=True, random_state=42)
        params = section["params"] or {"n_estimators": 150, "max_depth": 4}
        predicted = cross_val_predict(RandomForestRegressor(random_state=42, **params), X_arr, y_arr, cv=folds)
        baseline = np.full_like(y_arr, y_arr.mean())
        section["metrics"] = {
            "validation": "Validación cruzada de 5 particiones",
            "mae_min": round(float(mean_absolute_error(y_arr, predicted)), 2),
            "rmse_min": round(float(np.sqrt(mean_squared_error(y_arr, predicted))), 2),
            "r2": round(float(r2_score(y_arr, predicted)), 3),
            "baseline_mae_min": round(float(mean_absolute_error(y_arr, baseline)), 2),
            "mean_duration_min": round(float(y_arr.mean()), 1),
        }
        section["duration_by_weekday"] = [
            {"weekday": WEEKDAYS_ES[d], "avg_minutes": round(float(y_arr[X_arr[:, 0] == d].mean()), 1), "trips": int((X_arr[:, 0] == d).sum())}
            for d in range(7)
            if (X_arr[:, 0] == d).any()
        ]
    return section


def _anomaly_section(db: Session) -> dict[str, Any]:
    model = _load(ANOMALY_MODEL_PATH)
    speeds = np.array(
        [r[0] for r in db.query(GPSPosition.speed_kmh).filter(GPSPosition.speed_kmh.isnot(None)).all()],
        dtype=float,
    )
    section: dict[str, Any] = {
        "available": model is not None,
        "name": "Detección de velocidad anómala",
        "algorithm": "Isolation Forest (scikit-learn)",
        "task": "Detección de anomalías no supervisada",
        "target": "Marca cada posición GPS como normal o fuera de patrón",
        "features": ["Velocidad del bus (km/h) en cada posición GPS"],
        "params": (
            {"n_estimators": model.n_estimators, "contamination": model.contamination} if model is not None else {}
        ),
        "training_samples": int(speeds.size),
        "trained_at": _trained_at(ANOMALY_MODEL_PATH),
        "rules": {
            "high_kmh": HIGH_SPEED_THRESHOLD_KMH,
            "description": f"Además del modelo, toda velocidad > {HIGH_SPEED_THRESHOLD_KMH:.0f} km/h genera una alerta ALTA.",
        },
        "metrics": None,
        "normal_speed_range_kmh": None,
    }
    if model is None or speeds.size < 30:
        return section

    flagged = model.predict(speeds.reshape(-1, 1)) == -1
    reference = speeds > HIGH_SPEED_THRESHOLD_KMH
    section["metrics"] = {
        "reference": f"Regla de seguridad (> {HIGH_SPEED_THRESHOLD_KMH:.0f} km/h) usada como etiqueta de referencia",
        "flagged_rate": round(float(flagged.mean()), 4),
        "reference_rate": round(float(reference.mean()), 4),
        "precision": round(float(precision_score(reference, flagged, zero_division=0)), 3),
        "recall": round(float(recall_score(reference, flagged, zero_division=0)), 3),
        "f1": round(float(f1_score(reference, flagged, zero_division=0)), 3),
    }
    # Rango de velocidades que el modelo considera normal (barrido de 0 a 120 km/h).
    grid = np.arange(0, 121, 1.0).reshape(-1, 1)
    normal = grid[model.predict(grid) == 1].ravel()
    if normal.size:
        section["normal_speed_range_kmh"] = [float(normal.min()), float(normal.max())]
    return section


def model_summary(db: Session) -> dict[str, Any]:
    now = time.monotonic()
    if _cache["value"] is not None and now - _cache["at"] < CACHE_SECONDS:
        return _cache["value"]
    value = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "delay_model": _delay_section(db),
        "anomaly_model": _anomaly_section(db),
    }
    _cache.update(at=now, value=value)
    return value


def clear_summary_cache() -> None:
    _cache.update(at=0.0, value=None)
