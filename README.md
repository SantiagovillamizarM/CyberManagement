# CyberManagement

Sistema inteligente de gestión y control de gastos empresariales: los empleados registran
facturas por Telegram, Gemini extrae los datos y el administrador supervisa todo desde un
panel web privado.

Arquitectura y decisiones: [docs/01-analisis-arquitectura.md](docs/01-analisis-arquitectura.md)

## Requisitos

- Docker Desktop (con Docker Compose)
- Python 3.12 (solo para pruebas y scripts locales)
- Git

## Puesta en marcha (desarrollo)

1. Crear el archivo de variables de entorno a partir de la plantilla y completarlo:

   ```powershell
   Copy-Item .env.example .env
   ```

   Genera cada secreto con:

   ```powershell
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

2. Levantar los servicios:

   ```powershell
   docker compose up -d --build
   ```

   | Servicio | URL |
   |---|---|
   | API (salud) | http://127.0.0.1:8000/api/health |
   | API (documentación) | http://127.0.0.1:8000/api/docs |
   | n8n | http://127.0.0.1:5678 |

3. Preparar el entorno de Python local:

   ```powershell
   cd backend
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

4. Verificar las conexiones externas (PostgreSQL, Gemini, Telegram):

   ```powershell
   .venv\Scripts\python.exe -m scripts.check_services
   ```

5. Crear o actualizar las tablas de la base de datos (migraciones):

   ```powershell
   .venv\Scripts\alembic.exe upgrade head
   ```

6. Ejecutar las pruebas (usan una base separada, `cybermanagement_test`):

   ```powershell
   .venv\Scripts\python.exe -m pytest -v
   ```

## Comandos útiles

```powershell
docker compose ps              # estado de los servicios
docker compose logs -f api     # ver logs de la API en vivo
docker compose down            # detener (los datos se conservan)
```

> ⚠️ `docker compose down -v` **borra** los volúmenes, es decir, la base de datos y n8n.

### Cambios en los modelos

Después de modificar un archivo en `backend/app/models/`:

```powershell
cd backend
.venv\Scripts\alembic.exe revision --autogenerate -m "descripcion del cambio"
# Revisar el archivo generado en alembic/versions/ antes de aplicarlo
.venv\Scripts\alembic.exe upgrade head
```

La prueba `test_migrations_match_models` falla si se olvida crear la migración.

## Estructura

```
backend/   API FastAPI (app/), pruebas (tests/), scripts/
frontend/  Panel web (HTML, CSS, JS)            — Fase 7
n8n/       Workflows exportados en JSON          — Fase 6
infra/     Configuración de PostgreSQL, Caddy, backups
docs/      Documentación del proyecto
storage/   Archivos subidos en desarrollo (no se versiona)
```
