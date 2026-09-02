"""
Gateway WebSocket para el seguimiento GPS en tiempo real (Objetivo 2), con
detección de anomalías en vivo (Objetivo 4).

Un único endpoint por viaje: `/ws/trips/{trip_id}?token=...`

- El CONDUCTOR asignado a ese viaje se conecta y envía mensajes JSON con su
  posición (`{"lat": ..., "lon": ..., "speed_kmh": ..., "heading_deg": ...}`);
  cada mensaje se guarda en la tabla `gps_positions` y se retransmite a todos
  los demás conectados al mismo viaje. Además, cada posición se evalúa con el
  modelo de anomalías del Objetivo 4 (`app/ml/predict.py`); si la velocidad es
  anómala se crea un registro en `alerts` y se retransmite también un mensaje
  de tipo "alert" para que padres/monitores lo vean aparecer en el momento.
- Cualquier otro usuario del mismo colegio (ADMIN, MONITOR, o el PADRE de un
  estudiante de esa ruta) se conecta al mismo endpoint solo para "escuchar":
  sus mensajes entrantes se ignoran, solo reciben las posiciones que transmite
  el conductor.

Los mensajes que se retransmiten llevan un campo `"type"` ("position" o
"alert") para que el cliente (la app móvil) sepa cómo interpretarlos.

La autenticación va por query param (`?token=`) porque los clientes WebSocket
de React Native/Expo no siempre pueden fijar cabeceras HTTP personalizadas al
abrir la conexión; el token es el mismo JWT que ya emite `/api/v1/auth/login`.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.database import SessionLocal
from app.core.security import decode_access_token
from app.ml.predict import evaluate_speed_anomaly
from app.models.alert import Alert, AlertType
from app.models.gps_position import GPSPosition
from app.models.trip import Trip, TripStatus
from app.models.user import Role, User
from app.ws.manager import manager

router = APIRouter()

# Códigos de cierre propios (rango libre 4000-4999 de la especificación WebSocket)
CLOSE_UNAUTHORIZED = 4401
CLOSE_NOT_FOUND = 4404


@router.websocket("/trips/{trip_id}")
async def trip_gps_socket(websocket: WebSocket, trip_id: int, token: str = Query(...)) -> None:
    payload = decode_access_token(token)
    if payload is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    db = SessionLocal()
    try:
        user = db.get(User, int(payload["sub"]))
        trip = db.get(Trip, trip_id)

        # Aislamiento multi-tenant también en el WebSocket: un usuario nunca puede
        # conectarse al viaje de un colegio que no es el suyo.
        if user is None or trip is None or trip.tenant_id != user.tenant_id:
            await websocket.close(code=CLOSE_NOT_FOUND)
            return

        is_driver = user.role == Role.CONDUCTOR and trip.driver_id == user.id

        await manager.connect(trip_id, websocket)
        try:
            while True:
                data = await websocket.receive_json()

                if not is_driver:
                    # Quien no es el conductor asignado solo puede escuchar.
                    continue

                lat = float(data["lat"])
                lon = float(data["lon"])
                speed_kmh = data.get("speed_kmh")
                heading_deg = data.get("heading_deg")

                position = GPSPosition(
                    tenant_id=trip.tenant_id,
                    trip_id=trip.id,
                    geom=f"SRID=4326;POINT({lon} {lat})",
                    speed_kmh=speed_kmh,
                    heading_deg=heading_deg,
                )
                db.add(position)

                # El primer punto GPS que llega marca el inicio real del viaje,
                # por si el conductor no llamó explícitamente a /trips/{id}/start.
                if trip.status == TripStatus.SCHEDULED:
                    trip.status = TripStatus.IN_PROGRESS
                    trip.start_time = datetime.now(timezone.utc)

                # Objetivo 4: ¿esta velocidad es anómala para esta ruta? Si sí,
                # queda registrada como alerta permanente (tabla `alerts`), no
                # solo como un aviso que se pierde si nadie estaba mirando el
                # celular en ese momento.
                anomaly = evaluate_speed_anomaly(speed_kmh)
                alert_row: Alert | None = None
                if anomaly is not None:
                    alert_row = Alert(
                        tenant_id=trip.tenant_id,
                        trip_id=trip.id,
                        type=AlertType.ANOMALY,
                        severity=anomaly.severity,
                        description=anomaly.description,
                    )
                    db.add(alert_row)

                db.commit()

                await manager.broadcast(
                    trip_id,
                    {
                        "type": "position",
                        "trip_id": trip.id,
                        "lat": lat,
                        "lon": lon,
                        "speed_kmh": speed_kmh,
                        "heading_deg": heading_deg,
                        "recorded_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

                if alert_row is not None:
                    await manager.broadcast(
                        trip_id,
                        {
                            "type": "alert",
                            "trip_id": trip.id,
                            "alert_id": alert_row.id,
                            "alert_type": alert_row.type.value,
                            "severity": alert_row.severity.value,
                            "description": alert_row.description,
                            "created_at": alert_row.created_at.isoformat(),
                        },
                    )
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(trip_id, websocket)
    finally:
        db.close()
