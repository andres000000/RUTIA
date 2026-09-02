from geoalchemy2 import Geometry
from sqlalchemy import ForeignKey, Integer, String, Time
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.base import TenantMixin, TimestampMixin


class Route(TenantMixin, TimestampMixin, Base):
    """Una ruta escolar: el trazado que sigue un bus, asociado a un colegio (tenant)."""

    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    vehicle_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True
    )
    scheduled_start_time: Mapped[object | None] = mapped_column(Time, nullable=True)
    # Trazado geoespacial de la ruta (LineString en WGS84 / SRID 4326).
    geom: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="LINESTRING", srid=4326), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Route id={self.id} name={self.name!r}>"


class Stop(TenantMixin, TimestampMixin, Base):
    """Una parada dentro de una ruta, con su geocerca para detectar llegada/salida del bus."""

    __tablename__ = "stops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    route_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(150), nullable=False)
    order_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Ubicación de la parada (Point en WGS84 / SRID 4326).
    geom: Mapped[object] = mapped_column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    geofence_radius_m: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    def __repr__(self) -> str:
        return f"<Stop id={self.id} name={self.name!r} route_id={self.route_id}>"
