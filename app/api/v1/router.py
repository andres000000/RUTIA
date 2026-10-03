from fastapi import APIRouter

from app.api.v1 import alerts, auth, geocoding, ml, platform, routes, students, tenants, trips, users, vehicles

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(platform.router)
api_router.include_router(tenants.router)
api_router.include_router(users.router)
api_router.include_router(vehicles.router)
api_router.include_router(routes.router)
api_router.include_router(students.router)
api_router.include_router(geocoding.router)
api_router.include_router(trips.router)
api_router.include_router(alerts.router)
api_router.include_router(ml.router)
