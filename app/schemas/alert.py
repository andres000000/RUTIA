from datetime import datetime

from pydantic import BaseModel, Field

from app.models.alert import AlertSeverity, AlertType


class AlertRead(BaseModel):
    id: int
    trip_id: int | None = None
    student_id: int | None = None
    type: AlertType
    severity: AlertSeverity
    description: str
    created_at: datetime
    resolved_at: datetime | None = None

    model_config = {"from_attributes": True}


class AlertCreate(BaseModel):
    """Cuerpo de `POST /alerts` -- una incidencia que reporta a mano un
    MONITOR (o ADMIN) durante un viaje, a diferencia de las alertas de
    DELAY/ANOMALY/GEOFENCE_EXIT que genera solo el módulo de IA. La usa la
    app móvil para "Reportar alumno ausente" y "Reportar problema"."""

    trip_id: int | None = None
    student_id: int | None = None
    type: AlertType = AlertType.INCIDENT
    severity: AlertSeverity = AlertSeverity.LOW
    description: str = Field(..., min_length=1, max_length=500)
