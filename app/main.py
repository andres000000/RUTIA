from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import settings
from app.ws.gps import router as gps_ws_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Plataforma inteligente para la gestión y seguimiento en tiempo real de rutas escolares.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")
app.include_router(gps_ws_router, prefix="/ws")


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    return {"status": "ok", "project": settings.PROJECT_NAME}
