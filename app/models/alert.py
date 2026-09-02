import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantMixin


class AlertType(str, enum.Enum):
    DELAY = "DELAY"  # retraso predicho o detectado (Objetivo 4)
    ANOMALY = "ANOMALY"  # anomalía de velocidad/ruta detectada por el modelo de IA
    GEOFENCE_EXIT = "GEOFENCE_EXIT"  # el bus salió del corredor esperado de la ruta
    INCIDENT = "INCIDENT"  # incidencia reportada manualmente por el monitor


class AlertSeverity(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class Alert(TenantMixin, Base):
    """Una alerta o incidencia asociada (opcionalmente) a un viaje."""

    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    trip_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("trips.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # A qué estudiante se refiere la incidencia (ej. "alumno ausente"), cuando
    # aplica -- las alertas automáticas del módulo de IA (retraso, anomalía)
    # no tocan este campo, solo las que reporta un monitor a mano.
    student_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("students.id", ondelete="SET NULL"), nullable=True, index=True
    )
    type: Mapped[AlertType] = mapped_column(Enum(AlertType, name="alert_type"), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(
        Enum(AlertSeverity, name="alert_severity"), nullable=False, default=AlertSeverity.MEDIUM
    )
    description: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<Alert id={self.id} type={self.type} severity={self.severity}>"
