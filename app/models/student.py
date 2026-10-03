from sqlalchemy import Boolean, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Student(TenantMixin, TimestampMixin, Base):
    """Un estudiante inscrito en una ruta escolar, vinculado a un acudiente (User rol PADRE)."""

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    grade: Mapped[str | None] = mapped_column(String(30), nullable=True)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    route_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("routes.id", ondelete="SET NULL"), nullable=True
    )
    stop_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stops.id", ondelete="SET NULL"), nullable=True
    )
    # Dirección de la casa, tal como la escribe el admin. Su ubicación en el
    # mapa vive en la parada "de casa" del estudiante (Stop.student_id), que es
    # la que se integra al recorrido de la ruta.
    address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<Student id={self.id} full_name={self.full_name!r}>"
