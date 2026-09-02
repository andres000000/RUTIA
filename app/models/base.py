from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Agrega columnas de auditoría de creación/actualización a un modelo."""

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TenantMixin:
    """
    Agrega la columna tenant_id a un modelo, que es la base de la multi-tenencia:
    cada colegio (Tenant) solo puede ver y modificar filas con su propio tenant_id.
    Este aislamiento se aplica en app/api/deps.py (get_current_tenant_id) y en cada
    endpoint, filtrando siempre las consultas por tenant_id.
    """

    tenant_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
