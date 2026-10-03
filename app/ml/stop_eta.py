"""
ETA por parada (Fase 3 del mapa): cuántos minutos faltan para que el bus
llegue a CADA parada que le queda, no solo al final del recorrido.

Modelo: Random Forest de regresión con 4 variables por cada par
(posición actual del bus, parada objetivo):

1. Distancia restante (m): del bus a la próxima parada y de ahí, parada por
   parada, hasta la objetivo (en línea recta por tramo; el modelo aprende
   cuánto más largo es ir por las calles).
2. Paradas intermedias que faltan antes de la objetivo (cada una suma el
   tiempo de subir/bajar estudiantes).
3. Día de la semana y 4. hora (tráfico).

¿De dónde salen los ejemplos de entrenamiento? De los viajes terminados que
tienen GPS grabado: para cada posición se sabe a qué hora llegó realmente el
bus a cada parada siguiente (la primera posición dentro del radio de la
parada). Como las paradas pueden cambiar (el admin las edita), solo sirven
los viajes cuyas posiciones sí pasan por las paradas ACTUALES. Si eso deja
muy pocos ejemplos (el piloto lleva pocos días), se completan con viajes
simulados sobre las rutas actuales, igual que `generate_history.py` lo hace
para el modelo de duración. Cuántos viajes son reales y cuántos simulados
queda guardado con el modelo y se muestra en el panel (página "Modelo IA").

Uso: lo entrena `python -m app.ml.train` junto con los otros dos modelos.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import joblib
import numpy as np
from geoalchemy2.shape import to_shape
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import GroupKFold, cross_val_predict
from sqlalchemy.orm import Session

from app.ml.paths import STOP_ETA_MODEL_PATH
from app.models.gps_position import GPSPosition
from app.models.route import Route, Stop
from app.models.trip import Trip, TripStatus
from app.services.routing import haversine_m

Coord = tuple[float, float]  # (lon, lat)

FEATURE_NAMES = [
    "Distancia restante hasta la parada (m)",
    "Paradas intermedias antes de llegar",
    "Día de la semana",
    "Hora",
]

# El bus "llegó" a una parada cuando alguna posición GPS quedó a menos de esto
# (o del radio de geocerca de la parada, si es mayor). Con un punto GPS cada
# ~15 s a 30 km/h hay ~125 m entre puntos: un radio menor podría no ver nunca
# al bus pasar por la parada.
ARRIVAL_MIN_RADIUS_M = 100.0
# Un ejemplo de entrenamiento cada tantas posiciones (posiciones seguidas son
# casi idénticas y solo inflarían el conjunto de datos).
SAMPLE_EVERY = 4
# Un viaje grabado solo sirve si pasó por al menos esta fracción de las
# paradas actuales de su ruta.
MIN_MATCHED_STOPS_SHARE = 0.8
# Por debajo de esto, los ejemplos reales se completan con viajes simulados.
MIN_REAL_SAMPLES = 400
MAX_SAMPLES = 30_000
SIMULATED_TRIPS_PER_ROUTE = 45  # ~9 semanas de lunes a viernes
# Además de las rutas reales, rutas inventadas en Bucaramanga (3 a 10 paradas,
# tramos de 300 m a 1.5 km). Un Random Forest no extrapola: si solo viera la
# ruta de prueba local, no sabría estimar una ruta más larga en producción.
SIMULATED_EXTRA_ROUTES = 30
SIMULATED_TRIPS_PER_EXTRA_ROUTE = 12
BUCARAMANGA_CENTER: Coord = (-73.1227, 7.1193)

# Respaldo sin modelo entrenado: velocidad promedio de un bus escolar en
# ciudad y minutos por cada parada intermedia. También es la "línea base"
# contra la que se compara el modelo en la ficha técnica.
HEURISTIC_SPEED_KMH = 20.0
HEURISTIC_DWELL_MIN = 1.0
# Las calles son más largas que la línea recta entre paradas.
HEURISTIC_ROAD_FACTOR = 1.3


# ---------------------------------------------------------------------------
# Piezas comunes a entrenamiento e inferencia (mismo cálculo en los dos lados)
# ---------------------------------------------------------------------------


def arrival_radius_m(stop: Stop) -> float:
    return max(float(stop.geofence_radius_m or 0), ARRIVAL_MIN_RADIUS_M)


def match_arrivals(stop_coords: list[Coord], radii: list[float], coords: list[Coord]) -> list[int | None]:
    """Índice de la primera posición en que el bus llegó a cada parada, en
    orden: cada parada se busca DESPUÉS de la última encontrada, así una
    parada que el bus roza antes de tiempo no cuenta como visitada. Una parada
    que nunca alcanzó (por ejemplo, ese día no había a quién recoger) queda en
    None y no frena la búsqueda de las siguientes."""
    arrivals: list[int | None] = []
    cursor = 0
    for stop, radius in zip(stop_coords, radii):
        found = None
        for i in range(cursor, len(coords)):
            if haversine_m(coords[i], stop) <= radius:
                found = i
                break
        arrivals.append(found)
        if found is not None:
            cursor = found
    return arrivals


def next_stop_index(arrivals: list[int | None], position_index: int) -> int:
    """Primera parada que falta: la siguiente a la última ya alcanzada hasta
    esa posición (las saltadas antes de esa quedan atrás)."""
    reached = [j for j, a in enumerate(arrivals) if a is not None and a <= position_index]
    return reached[-1] + 1 if reached else 0


def remaining_distance_m(bus: Coord, stop_coords: list[Coord], next_idx: int, target_idx: int) -> float:
    distance = haversine_m(bus, stop_coords[next_idx])
    for k in range(next_idx, target_idx):
        distance += haversine_m(stop_coords[k], stop_coords[k + 1])
    return distance


def features(remaining_m: float, stops_before: int, when: dt.datetime) -> list[float]:
    return [remaining_m, float(stops_before), float(when.weekday()), float(when.hour)]


def heuristic_minutes(remaining_m: float, stops_before: int) -> float:
    travel = remaining_m * HEURISTIC_ROAD_FACTOR / (HEURISTIC_SPEED_KMH * 1000 / 60)
    return travel + stops_before * HEURISTIC_DWELL_MIN


# ---------------------------------------------------------------------------
# Construcción del conjunto de datos
# ---------------------------------------------------------------------------


def samples_from_track(
    stop_coords: list[Coord], radii: list[float], times: list[dt.datetime], coords: list[Coord]
) -> list[tuple[list[float], float]]:
    """Ejemplos (variables, minutos reales hasta llegar) de un viaje grabado."""
    arrivals = match_arrivals(stop_coords, radii, coords)
    matched = sum(a is not None for a in arrivals)
    if matched < max(2, math.ceil(MIN_MATCHED_STOPS_SHARE * len(stop_coords))):
        # El viaje no pasó por (casi todas) estas paradas: se grabó con otro
        # recorrido -- p. ej. antes de que el admin cambiara las paradas -- y
        # enseñaría tiempos de una ruta que ya no existe.
        return []
    samples = []
    for i in range(0, len(coords), SAMPLE_EVERY):
        next_idx = next_stop_index(arrivals, i)
        for j in range(next_idx, len(stop_coords)):
            arrived = arrivals[j]
            if arrived is None or arrived <= i:
                continue
            minutes = (times[arrived] - times[i]).total_seconds() / 60
            remaining = remaining_distance_m(coords[i], stop_coords, next_idx, j)
            samples.append((features(remaining, j - next_idx, times[i]), minutes))
    return samples


def _weekday_factor(weekday: int) -> float:
    # Mismo patrón semanal que generate_history.py (lunes más pesado).
    return {0: 1.35, 1: 1.05, 2: 1.0, 3: 1.05, 4: 0.9}.get(weekday, 1.0)


def simulate_track(
    stop_coords: list[Coord], start: dt.datetime, rng: random.Random
) -> tuple[list[dt.datetime], list[Coord]]:
    """Un viaje simulado sobre las paradas actuales, con un punto GPS cada 15 s:
    en cada parada el bus espera (suben/bajan estudiantes) y entre paradas
    avanza a una velocidad de ciudad que cambia según el día y el tramo. Las
    calles son más largas que la recta entre paradas (factor 1.2–1.5)."""
    step_s = 15
    day_factor = _weekday_factor(start.weekday()) * max(rng.gauss(1.0, 0.1), 0.7)
    times: list[dt.datetime] = []
    coords: list[Coord] = []
    now = start
    for k, stop in enumerate(stop_coords):
        dwell_s = rng.uniform(30, 120) if 0 < k < len(stop_coords) - 1 else 20
        for _ in range(max(int(dwell_s // step_s), 1)):
            times.append(now)
            coords.append(stop)
            now += dt.timedelta(seconds=step_s)
        if k == len(stop_coords) - 1:
            break
        nxt = stop_coords[k + 1]
        road_m = haversine_m(stop, nxt) * rng.uniform(1.2, 1.5)
        speed_kmh = max(rng.gauss(24, 4) / day_factor, 8)
        n = max(int(road_m / (speed_kmh / 3.6) // step_s), 1)
        for i in range(1, n + 1):
            f = i / (n + 1)
            times.append(now)
            coords.append((stop[0] + (nxt[0] - stop[0]) * f, stop[1] + (nxt[1] - stop[1]) * f))
            now += dt.timedelta(seconds=step_s)
    return times, coords


def random_route(rng: random.Random) -> list[Coord]:
    """Paradas de una ruta inventada: un paseo aleatorio desde un punto
    cercano al centro de Bucaramanga."""
    lon = BUCARAMANGA_CENTER[0] + rng.uniform(-0.03, 0.03)
    lat = BUCARAMANGA_CENTER[1] + rng.uniform(-0.03, 0.03)
    stops = [(lon, lat)]
    heading = rng.uniform(0, 2 * math.pi)
    for _ in range(rng.randint(2, 9)):
        heading += rng.uniform(-1.0, 1.0)
        leg_m = rng.uniform(300, 1500)
        lat += leg_m * math.sin(heading) / 110_540
        lon += leg_m * math.cos(heading) / (111_320 * math.cos(math.radians(lat)))
        stops.append((lon, lat))
    return stops


def _simulate_route_trips(
    stop_coords: list[Coord], radii: list[float], scheduled: dt.time, trips: int, rng: random.Random, group: int
) -> tuple[list[tuple[list[float], float, int]], int]:
    """Simula `trips` viajes de lunes a viernes hacia atrás desde hoy.
    Devuelve los ejemplos y el siguiente id de grupo libre (negativos)."""
    rows = []
    day = dt.date.today()
    made = 0
    while made < trips:
        day -= dt.timedelta(days=1)
        if day.weekday() >= 5:
            continue
        start = dt.datetime.combine(day, scheduled, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=rng.gauss(0, 3))
        times, coords = simulate_track(stop_coords, start, rng)
        rows.extend((x, y, group) for x, y in samples_from_track(stop_coords, radii, times, coords))
        group -= 1
        made += 1
    return rows, group


def _route_stops(db: Session, route_id: int) -> list[Stop]:
    return db.query(Stop).filter(Stop.route_id == route_id).order_by(Stop.order_index, Stop.id).all()


def _stop_coords(stops: list[Stop]) -> list[Coord]:
    return [(p.x, p.y) for p in (to_shape(s.geom) for s in stops)]


@dataclass
class Dataset:
    X: np.ndarray
    y: np.ndarray
    groups: np.ndarray
    real_trips: int
    simulated_trips: int


def build_dataset(db: Session, seed: int = 42) -> Dataset:
    rows: list[tuple[list[float], float, int]] = []
    stops_by_route: dict[int, list[Stop]] = {}

    def stops_for(route_id: int) -> list[Stop]:
        if route_id not in stops_by_route:
            stops_by_route[route_id] = _route_stops(db, route_id)
        return stops_by_route[route_id]

    real_trips = 0
    trips = (
        db.query(Trip)
        .filter(Trip.status == TripStatus.COMPLETED, Trip.start_time.isnot(None))
        .order_by(Trip.id)
        .all()
    )
    for trip in trips:
        stops = stops_for(trip.route_id)
        if len(stops) < 2:
            continue
        positions = (
            db.query(GPSPosition)
            .filter(GPSPosition.trip_id == trip.id)
            .order_by(GPSPosition.recorded_at)
            .all()
        )
        if len(positions) < 2:
            continue
        coords = [(p.x, p.y) for p in (to_shape(pos.geom) for pos in positions)]
        times = [pos.recorded_at for pos in positions]
        samples = samples_from_track(_stop_coords(stops), [arrival_radius_m(s) for s in stops], times, coords)
        if samples:
            real_trips += 1
            rows.extend((x, y, trip.id) for x, y in samples)

    simulated_trips = 0
    if len(rows) < MIN_REAL_SAMPLES:
        rng = random.Random(seed)
        group = -1  # grupos negativos = viajes simulados (no chocan con ids reales)
        for route in db.query(Route).order_by(Route.id).all():
            stops = stops_for(route.id)
            if len(stops) < 2:
                continue
            new_rows, group = _simulate_route_trips(
                _stop_coords(stops),
                [arrival_radius_m(s) for s in stops],
                route.scheduled_start_time or dt.time(6, 30),
                SIMULATED_TRIPS_PER_ROUTE,
                rng,
                group,
            )
            rows.extend(new_rows)
            simulated_trips += SIMULATED_TRIPS_PER_ROUTE
        for _ in range(SIMULATED_EXTRA_ROUTES):
            stop_coords = random_route(rng)
            scheduled = dt.time(6, rng.randint(0, 59))
            new_rows, group = _simulate_route_trips(
                stop_coords,
                [ARRIVAL_MIN_RADIUS_M] * len(stop_coords),
                scheduled,
                SIMULATED_TRIPS_PER_EXTRA_ROUTE,
                rng,
                group,
            )
            rows.extend(new_rows)
            simulated_trips += SIMULATED_TRIPS_PER_EXTRA_ROUTE

    if len(rows) > MAX_SAMPLES:
        rows = random.Random(seed).sample(rows, MAX_SAMPLES)
    X = np.array([r[0] for r in rows], dtype=float).reshape(-1, len(FEATURE_NAMES))
    y = np.array([r[1] for r in rows], dtype=float)
    groups = np.array([r[2] for r in rows])
    return Dataset(X=X, y=y, groups=groups, real_trips=real_trips, simulated_trips=simulated_trips)


# ---------------------------------------------------------------------------
# Entrenamiento
# ---------------------------------------------------------------------------


def _new_model() -> RandomForestRegressor:
    return RandomForestRegressor(n_estimators=100, max_depth=10, min_samples_leaf=20, random_state=42, n_jobs=-1)


def train_stop_eta_model(db: Session, path=None) -> int:
    """Entrena y guarda el modelo junto con su ficha (métricas de validación
    cruzada calculadas al entrenar). Devuelve cuántos ejemplos usó."""
    path = path or STOP_ETA_MODEL_PATH
    data = build_dataset(db)
    if len(data.y) < 50:
        print("No hay suficientes paradas/viajes para entrenar el modelo de ETA por parada.")
        return 0

    # Validación agrupada por viaje: todos los ejemplos de un mismo viaje caen
    # en la misma partición, así el modelo nunca se evalúa sobre un viaje del
    # que vio otras posiciones al entrenar (eso inflaría las métricas).
    metrics = None
    n_groups = len(set(data.groups.tolist()))
    if n_groups >= 5:
        predicted = cross_val_predict(_new_model(), data.X, data.y, cv=GroupKFold(n_splits=5), groups=data.groups)
        baseline = np.array([heuristic_minutes(x[0], int(x[1])) for x in data.X])
        metrics = {
            "validation": "Validación cruzada de 5 particiones agrupada por viaje",
            "mae_min": round(float(mean_absolute_error(data.y, predicted)), 2),
            "r2": round(float(r2_score(data.y, predicted)), 3),
            "baseline": f"Regla fija: distancia a {HEURISTIC_SPEED_KMH:.0f} km/h + {HEURISTIC_DWELL_MIN:.0f} min por parada",
            "baseline_mae_min": round(float(mean_absolute_error(data.y, baseline)), 2),
            "mean_target_min": round(float(data.y.mean()), 1),
        }

    model = _new_model().fit(data.X, data.y)
    bundle = {
        "model": model,
        "features": FEATURE_NAMES,
        "trained_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "samples": int(len(data.y)),
        "real_trips": data.real_trips,
        "simulated_trips": data.simulated_trips,
        "metrics": metrics,
        "feature_importance": [
            {"feature": name, "importance": round(float(imp), 3)}
            for name, imp in zip(FEATURE_NAMES, model.feature_importances_)
        ],
        "params": {"n_estimators": model.n_estimators, "max_depth": model.max_depth},
    }
    joblib.dump(bundle, path, compress=3)  # comprimido: el archivo va en el repo
    print(
        f"Modelo de ETA por parada entrenado con {len(data.y)} ejemplos "
        f"({data.real_trips} viajes reales, {data.simulated_trips} simulados) -> {path}"
    )
    return int(len(data.y))


# ---------------------------------------------------------------------------
# Inferencia
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def load_bundle(path_str: str | None = None) -> dict[str, Any] | None:
    path = STOP_ETA_MODEL_PATH if path_str is None else type(STOP_ETA_MODEL_PATH)(path_str)
    if not path.exists():
        return None
    try:
        bundle = joblib.load(path)
        return bundle if isinstance(bundle, dict) and "model" in bundle else None
    except Exception:
        return None


def current_bundle() -> dict[str, Any] | None:
    # Se pasa la ruta como texto para que las pruebas, que cambian
    # STOP_ETA_MODEL_PATH, no reciban un modelo cacheado de otra ruta.
    return load_bundle(str(STOP_ETA_MODEL_PATH))


@dataclass
class StopEta:
    stop: Stop
    status: str  # "passed" | "skipped" | "upcoming"
    arrived_at: dt.datetime | None = None
    eta_at: dt.datetime | None = None
    eta_minutes: float | None = None
    remaining_distance_m: float | None = None
    stops_before: int | None = None


@dataclass
class TripStopEtas:
    available: bool
    message: str | None
    source: str | None  # "model" | "heuristic"
    last_position_at: dt.datetime | None
    stops: list[StopEta]


def predict_stop_etas(db: Session, trip: Trip, now: dt.datetime | None = None) -> TripStopEtas:
    """Nunca lanza excepción por el modelo: sin modelo entrenado usa la regla
    fija (distancia/velocidad promedio + tiempo por parada) y lo dice en
    `source`, igual que el ETA del viaje completo."""
    now = now or dt.datetime.now(dt.timezone.utc)
    stops = _route_stops(db, trip.route_id)
    if len(stops) < 2:
        return TripStopEtas(False, "La ruta todavía no tiene paradas suficientes.", None, None, [])

    positions = (
        db.query(GPSPosition).filter(GPSPosition.trip_id == trip.id).order_by(GPSPosition.recorded_at).all()
    )
    if not positions:
        message = (
            "El viaje todavía no ha empezado."
            if trip.status == TripStatus.SCHEDULED
            else "El bus todavía no ha enviado su ubicación."
        )
        return TripStopEtas(False, message, None, None, [StopEta(stop=s, status="upcoming") for s in stops])

    stop_coords = _stop_coords(stops)
    coords = [(p.x, p.y) for p in (to_shape(pos.geom) for pos in positions)]
    arrivals = match_arrivals(stop_coords, [arrival_radius_m(s) for s in stops], coords)
    last_index = len(coords) - 1
    next_idx = next_stop_index(arrivals, last_index)
    last = positions[-1]

    result: list[StopEta] = []
    for j, stop in enumerate(stops[:next_idx]):
        if arrivals[j] is not None:
            result.append(StopEta(stop=stop, status="passed", arrived_at=positions[arrivals[j]].recorded_at))
        else:
            result.append(StopEta(stop=stop, status="skipped"))

    upcoming = list(range(next_idx, len(stops)))
    if trip.status != TripStatus.IN_PROGRESS or not upcoming:
        result.extend(StopEta(stop=stops[j], status="upcoming") for j in upcoming)
        message = None if not upcoming else "El viaje no está en curso."
        return TripStopEtas(trip.status == TripStatus.IN_PROGRESS, message, None, last.recorded_at, result)

    bus = coords[-1]
    distances = [remaining_distance_m(bus, stop_coords, next_idx, j) for j in upcoming]
    stops_before = [j - next_idx for j in upcoming]

    bundle = current_bundle()
    if bundle is not None:
        X = np.array([features(d, b, last.recorded_at) for d, b in zip(distances, stops_before)])
        minutes = [float(m) for m in bundle["model"].predict(X)]
        source = "model"
    else:
        minutes = [heuristic_minutes(d, b) for d, b in zip(distances, stops_before)]
        source = "heuristic"

    running_max = 0.0
    for j, d, b, m in zip(upcoming, distances, stops_before, minutes):
        # Una parada más adelante nunca puede llegar antes que la anterior.
        running_max = max(running_max, m, 0.0)
        eta_at = last.recorded_at + dt.timedelta(minutes=running_max)
        result.append(
            StopEta(
                stop=stops[j],
                status="upcoming",
                eta_at=eta_at,
                eta_minutes=round(max((eta_at - now).total_seconds() / 60, 0.0), 1),
                remaining_distance_m=round(d, 1),
                stops_before=b,
            )
        )
    return TripStopEtas(True, None, source, last.recorded_at, result)
