"""
Script de datos semilla para RUTIA.

Crea el tenant de demostración "Colegio Nijepra" (el colegio del piloto) con:
- un usuario ADMIN
- un conductor y un monitor
- un bus
- una ruta con 2 paradas
- un padre de familia y un estudiante inscrito en la ruta
- un viaje (Trip) del día de hoy, listo para probar el WebSocket de GPS en vivo

Uso:
    python -m app.seed
"""

import datetime as dt

from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.route import Route, Stop
from app.models.student import Student
from app.models.tenant import Tenant
from app.models.trip import Trip
from app.models.user import Role, User
from app.models.vehicle import Vehicle

DEMO_TENANT_NAME = "Colegio Nijepra"


def get_or_create_tenant(db: Session) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.name == DEMO_TENANT_NAME).first()
    if tenant:
        return tenant
    tenant = Tenant(name=DEMO_TENANT_NAME, address="Bucaramanga, Santander")
    db.add(tenant)
    db.flush()
    return tenant


def get_or_create_user(
    db: Session, tenant_id: int, email: str, full_name: str, role: Role, password: str = "rutia1234"
) -> User:
    user = db.query(User).filter(User.email == email).first()
    if user:
        return user
    user = User(
        tenant_id=tenant_id,
        email=email,
        hashed_password=hash_password(password),
        full_name=full_name,
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def run() -> None:
    db = SessionLocal()
    try:
        tenant = get_or_create_tenant(db)

        admin = get_or_create_user(db, tenant.id, "admin@nijepra.edu.co", "Admin Nijepra", Role.ADMIN)
        conductor = get_or_create_user(
            db, tenant.id, "conductor@nijepra.edu.co", "Carlos Conductor", Role.CONDUCTOR
        )
        monitor = get_or_create_user(
            db, tenant.id, "monitor@nijepra.edu.co", "Marta Monitor", Role.MONITOR
        )
        padre = get_or_create_user(db, tenant.id, "padre@nijepra.edu.co", "Pedro Padre", Role.PADRE)

        vehicle = db.query(Vehicle).filter(Vehicle.tenant_id == tenant.id).first()
        if not vehicle:
            vehicle = Vehicle(
                tenant_id=tenant.id,
                plate="RUT-001",
                capacity=25,
                driver_id=conductor.id,
                monitor_id=monitor.id,
            )
            db.add(vehicle)
            db.flush()

        route = db.query(Route).filter(Route.tenant_id == tenant.id).first()
        if not route:
            route = Route(tenant_id=tenant.id, name="Ruta 1 - Cabecera", vehicle_id=vehicle.id)
            db.add(route)
            db.flush()

            stop1 = Stop(
                tenant_id=tenant.id,
                route_id=route.id,
                name="Parada Parque Nijepra",
                order_index=1,
                geom="SRID=4326;POINT(-73.1198 7.1193)",
                geofence_radius_m=100,
            )
            stop2 = Stop(
                tenant_id=tenant.id,
                route_id=route.id,
                name="Colegio Nijepra - Portería",
                order_index=2,
                geom="SRID=4326;POINT(-73.1254 7.1258)",
                geofence_radius_m=80,
            )
            db.add_all([stop1, stop2])
            db.flush()

        student = db.query(Student).filter(Student.tenant_id == tenant.id).first()
        if not student:
            student = Student(
                tenant_id=tenant.id,
                full_name="Sofía Pérez",
                grade="5°",
                parent_id=padre.id,
                route_id=route.id,
            )
            db.add(student)
            db.flush()

        trip = (
            db.query(Trip)
            .filter(Trip.tenant_id == tenant.id, Trip.date == dt.date.today())
            .first()
        )
        if not trip:
            trip = Trip(
                tenant_id=tenant.id,
                route_id=route.id,
                vehicle_id=vehicle.id,
                driver_id=conductor.id,
                date=dt.date.today(),
            )
            db.add(trip)
            db.flush()

        db.commit()

        print("Seed completado.")
        print(f"Tenant: {tenant.name} (id={tenant.id})")
        print(f"Admin: {admin.email} / rutia1234")
        print(f"Conductor: {conductor.email} / rutia1234")
        print(f"Monitor: {monitor.email} / rutia1234")
        print(f"Padre: {padre.email} / rutia1234")
        print(f"Trip de hoy listo para probar el WebSocket: trip_id={trip.id}")
        print(f"  ws://localhost:8000/ws/trips/{trip.id}?token=<TOKEN_DEL_CONDUCTOR>")
    finally:
        db.close()


if __name__ == "__main__":
    run()
