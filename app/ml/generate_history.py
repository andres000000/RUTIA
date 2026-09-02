"""
Genera un histórico sintético de viajes y posiciones GPS.

¿Por qué hace falta esto? El Objetivo 4 pide predicción de retrasos y
detección de anomalías, pero eso requiere datos históricos — y el piloto en
el Colegio Nijepra todavía no ha corrido lo suficiente como para acumularlos
por sí solo. Este script simula ~9 semanas de viajes pasados (de lunes a
viernes, como un colegio real) con variaciones realistas de retraso según el
día de la semana, para que el modelo tenga algo significativo que aprender
desde el primer día. Cuando el piloto empiece a generar datos reales, el
mismo `train.py` los usará automáticamente (ver `MIN_REAL_TRIPS_TO_PREFER_REAL`
en `train.py`) — esto es solo para no arrancar con el modelo completamente en
blanco.

Es idempotente: si ya existe un viaje para una fecha dada, no lo duplica, así
que correr esto varias veces no genera datos repetidos.
"""

import datetime as dt
import random

from sqlalchemy.orm import Session

from app.models.route import Route, Stop
from app.models.trip import Trip, TripStatus
from app.models.gps_position import GPSPosition

# Cuántas semanas hacia atrás simular.
HISTORY_WEEKS = 9

# Duración base de un tramo entre dos paradas consecutivas, en minutos, antes
# de aplicarle las variaciones de tráfico/retraso del día.
BASE_LEG_MINUTES = 9.0

# Un punto GPS cada este número de segundos simulados (igual que el intervalo
# real que usa la app móvil del conductor).
GPS_INTERVAL_SECONDS = 15


def _ordered_stops(db: Session, route: Route) -> list[Stop]:
    return db.query(Stop).filter(Stop.route_id == route.id).order_by(Stop.order_index).all()


def _interpolate(a: tuple[float, float], b: tuple[float, float], fraction: float) -> tuple[float, float]:
    return (a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction)


def _day_delay_factor(weekday: int) -> float:
    """Los lunes hay más tráfico/retraso, los viernes un poco menos actividad
    a media mañana; el resto de la semana es el caso "normal". Esto le da al
    modelo una relación real que aprender entre día de la semana y retraso."""
    return {0: 1.35, 1: 1.05, 2: 1.0, 3: 1.05, 4: 0.9}.get(weekday, 1.0)


def generate_history_for_route(
    db: Session, route: Route, *, weeks: int = HISTORY_WEEKS, seed: int | None = 42
) -> int:
    """Crea viajes históricos COMPLETED + sus posiciones GPS para una ruta.
    Devuelve cuántos viajes nuevos se crearon (0 si ya existían todos)."""
    rng = random.Random(seed)
    stops = _ordered_stops(db, route)
    if len(stops) < 2:
        return 0

    from geoalchemy2.shape import to_shape

    from app.models.vehicle import Vehicle

    stop_coords = [(to_shape(s.geom).x, to_shape(s.geom).y) for s in stops]  # (lon, lat)

    driver_id = None
    if route.vehicle_id is not None:
        vehicle = db.get(Vehicle, route.vehicle_id)
        driver_id = vehicle.driver_id if vehicle else None

    scheduled_time = route.scheduled_start_time or dt.time(6, 30)
    today = dt.date.today()
    created = 0

    for days_ago in range(1, weeks * 7 + 1):
        trip_date = today - dt.timedelta(days=days_ago)
        if trip_date.weekday() >= 5:  # sábado/domingo: no hay ruta escolar
            continue

        already_exists = (
            db.query(Trip.id)
            .filter(Trip.tenant_id == route.tenant_id, Trip.route_id == route.id, Trip.date == trip_date)
            .first()
        )
        if already_exists:
            continue

        # Un pequeño porcentaje de días con una anomalía clara (velocidad muy
        # alta en algún tramo) — le da al detector de anomalías ejemplos
        # reales de "esto no es normal" para aprender, en vez de solo ruido.
        is_anomalous_day = rng.random() < 0.06

        delay_factor = _day_delay_factor(trip_date.weekday())
        # Variación aleatoria del día encima del factor sistemático (tráfico
        # imprevisto, clima, etc.).
        delay_factor *= rng.gauss(1.0, 0.12)
        delay_factor = max(delay_factor, 0.7)

        jitter_minutes = rng.gauss(0, 3)
        start_dt = dt.datetime.combine(trip_date, scheduled_time, tzinfo=dt.timezone.utc) + dt.timedelta(
            minutes=jitter_minutes
        )

        trip = Trip(
            tenant_id=route.tenant_id,
            route_id=route.id,
            vehicle_id=route.vehicle_id,
            driver_id=driver_id,
            date=trip_date,
            status=TripStatus.COMPLETED,
            start_time=start_dt,
        )
        db.add(trip)
        db.flush()  # para tener trip.id antes de crear las posiciones

        positions: list[GPSPosition] = []
        current_time = start_dt
        anomalous_point_index = (
            rng.randrange(2, max(3, int(BASE_LEG_MINUTES * 60 / GPS_INTERVAL_SECONDS) - 2))
            if is_anomalous_day
            else -1
        )
        point_counter = 0

        for leg_index in range(len(stop_coords) - 1):
            leg_minutes = BASE_LEG_MINUTES * delay_factor * rng.gauss(1.0, 0.08)
            leg_seconds = max(leg_minutes * 60, 60)
            num_points = max(int(leg_seconds / GPS_INTERVAL_SECONDS), 4)

            for i in range(num_points):
                fraction = i / (num_points - 1)
                lon, lat = _interpolate(stop_coords[leg_index], stop_coords[leg_index + 1], fraction)

                # Velocidad "normal": una campana alrededor de 28 km/h, algo
                # más lenta al arrancar/parar en cada extremo del tramo.
                edge_slowdown = 1 - 0.5 * (1 - abs(fraction - 0.5) * 2)
                speed = max(rng.gauss(28, 4) * edge_slowdown, 3)

                if point_counter == anomalous_point_index:
                    speed = rng.uniform(85, 105)  # anomalía inyectada a propósito

                positions.append(
                    GPSPosition(
                        tenant_id=route.tenant_id,
                        trip_id=trip.id,
                        geom=f"SRID=4326;POINT({lon} {lat})",
                        speed_kmh=round(speed, 1),
                        heading_deg=None,
                        recorded_at=current_time,
                    )
                )
                current_time += dt.timedelta(seconds=GPS_INTERVAL_SECONDS)
                point_counter += 1

        trip.end_time = current_time
        db.add_all(positions)
        db.commit()
        created += 1

    return created


def generate_history_for_tenant(db: Session, tenant_id: int, **kwargs) -> int:
    routes = db.query(Route).filter(Route.tenant_id == tenant_id).all()
    total = 0
    for route in routes:
        total += generate_history_for_route(db, route, **kwargs)
    return total
