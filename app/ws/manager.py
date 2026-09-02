"""
Gestor de conexiones WebSocket en memoria, agrupadas por viaje (trip_id).

Alcance deliberado para este proyecto: al ser un solo proceso (un backend, sin
réplicas horizontales), un diccionario en memoria es suficiente y evita la
complejidad de un message broker externo (Redis pub/sub, etc.) para el piloto.
Si más adelante RUTIA necesita correr en varias instancias del backend a la vez,
este es el punto donde se reemplazaría por un backend de pub/sub compartido.
"""

from fastapi import WebSocket


class TripConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[int, list[WebSocket]] = {}

    async def connect(self, trip_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(trip_id, []).append(websocket)

    def disconnect(self, trip_id: int, websocket: WebSocket) -> None:
        connections = self._connections.get(trip_id)
        if not connections:
            return
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self._connections.pop(trip_id, None)

    async def broadcast(self, trip_id: int, message: dict) -> None:
        for websocket in list(self._connections.get(trip_id, [])):
            try:
                await websocket.send_json(message)
            except Exception:
                self.disconnect(trip_id, websocket)


manager = TripConnectionManager()
