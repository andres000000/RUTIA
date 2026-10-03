"""Rutas de archivos compartidas entre el entrenamiento y la inferencia."""

from pathlib import Path

MODEL_DIR = Path(__file__).resolve().parent / "trained_models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)

DELAY_MODEL_PATH = MODEL_DIR / "delay_model.joblib"
ANOMALY_MODEL_PATH = MODEL_DIR / "anomaly_model.joblib"
STOP_ETA_MODEL_PATH = MODEL_DIR / "stop_eta_model.joblib"
