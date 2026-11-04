"""Punto de entrada de la API de CyberManagement."""

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

from app.api import health
from app.api.internal import documents as internal_documents
from app.api.internal import registration as internal_registration
from app.api.internal import telegram as internal_telegram
from app.api.v1 import auth, catalog, documents, employees, expenses, viaticos
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

# Política de contenido del panel: solo se ejecuta código servido por nosotros (sin
# scripts en línea ni de terceros). Si alguien lograra inyectar HTML, no podría ejecutar JS.
PANEL_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'self'; "
    "form-action 'self'; frame-ancestors 'none'"
)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"  # no adivinar tipos de archivo
    response.headers["X-Frame-Options"] = "DENY"  # nadie puede incrustar el panel en un iframe
    response.headers["Referrer-Policy"] = "same-origin"
    path = request.url.path
    if path.startswith("/api/v1"):
        response.headers["Cache-Control"] = "no-store"  # datos privados: no guardar en caché
    elif not path.startswith("/api"):  # páginas del panel (la doc /api/docs usa su propio CDN)
        response.headers.setdefault("Content-Security-Policy", PANEL_CSP)
    return response


app.include_router(health.router, prefix="/api")
app.include_router(auth.router, prefix="/api/v1")
app.include_router(employees.router, prefix="/api/v1")
app.include_router(catalog.router, prefix="/api/v1")
app.include_router(viaticos.router, prefix="/api/v1")
app.include_router(expenses.router, prefix="/api/v1")
app.include_router(documents.router, prefix="/api/v1")

# Endpoints internos: solo n8n (token interno). En producción, Caddy NO los expone.
app.include_router(internal_registration.router, prefix="/api/internal")
app.include_router(internal_documents.router, prefix="/api/internal")
app.include_router(internal_telegram.router, prefix="/api/internal")

# Panel web. Se monta AL FINAL para que /api/* tenga prioridad.
# En producción lo servirá Caddy directamente (Fase 10).
if settings.frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="panel")
