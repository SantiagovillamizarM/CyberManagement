"""Punto de entrada de la API de CyberManagement."""

from fastapi import FastAPI

from app.api import health
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

app.include_router(health.router, prefix="/api")
