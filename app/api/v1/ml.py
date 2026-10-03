from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.database import get_db
from app.ml.summary import model_summary
from app.models.user import Role

router = APIRouter(prefix="/ml", tags=["ml"])


@router.get("/summary")
def get_model_summary(
    db: Session = Depends(get_db),
    _admin=Depends(require_roles(Role.ADMIN)),
) -> dict[str, Any]:
    """Ficha técnica y métricas de los dos modelos de IA (Objetivo 4): qué
    algoritmo son, qué variables usan, con cuántos datos se entrenaron y qué
    tan bien funcionan. La usan la sección "Modelo" y el reporte PDF del panel.

    Los modelos se entrenan con el histórico de toda la plataforma (no por
    colegio), así que las métricas son globales; no expone datos individuales
    de ningún colegio, solo agregados."""
    return model_summary(db)
