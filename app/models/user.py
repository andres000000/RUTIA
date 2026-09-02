import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Role(str, enum.Enum):
    ADMIN = "ADMIN"  # gestiona un colegio desde la web admin
    PADRE = "PADRE"  # consulta rutas y recibe alertas en la app móvil
    CONDUCTOR = "CONDUCTOR"  # inicia/termina viajes y transmite GPS desde la app móvil
    MONITOR = "MONITOR"  # reporta incidencias desde la app móvil


class User(TenantMixin, TimestampMixin, Base):
    """Usuario de la plataforma. Todo usuario pertenece a un único colegio (tenant)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    email: Mapped[str] = mapped_column(String(150), unique=True, index=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    role: Mapped[Role] = mapped_column(Enum(Role, name="user_role"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Protección contra fuerza bruta en el login (ver app/api/v1/auth.py):
    # cada contraseña incorrecta suma uno; al llegar al umbral, la cuenta queda
    # bloqueada temporalmente (locked_until) sin importar que después se
    # ingrese la contraseña correcta durante el bloqueo.
    failed_login_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email!r} role={self.role}>"
