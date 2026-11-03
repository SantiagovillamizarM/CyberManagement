"""Punto de entrada de la API de CyberManagement."""

from fastapi import FastAPI, Request, Response

from app.api import health
from app.api.internal import registration as internal_registration
from app.api.v1 import auth, employees
from app.core.config import get_settings
from app.core.logging import setup_logging

settings = get_settings()
setup_logging(settings.log_level)

app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    # La documentación interactiva solo se publica fuera de producción.
    docs_url=None if settings.is_production else "/api/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/api/openapi.json",
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"  # no adivinar tipos de archivo
    response.headers["X-Frame-Options"] = "DENY"  # nadie puede incrustar el panel en un iframe
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/api/v1"):
        response.headers["Cache-Control"] = "no-store"  # datos privados: no guardar en caché
    return response


app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(employees.router, prefix="/api/v1")

# Endpoints internos: solo n8n (token interno). En producción, Caddy NO los expone.
app.include_router(internal_registration.router, prefix="/api/internal")
