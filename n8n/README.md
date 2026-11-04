# Workflows de n8n

Los workflows se guardan aquí en JSON y se versionan con Git. **No contienen reglas de negocio**:
solo reciben, llaman a Python y ejecutan las acciones que Python devuelve.

| Archivo | Workflow | Qué hace |
|---|---|---|
| `cm-02-entrada-telegram.json` | CM-02 · Entrada Telegram | Recibe todos los mensajes del bot, los envía a Python y ejecuta las acciones de respuesta |

## Variables de entorno que usan (definidas en `docker-compose.yml`)

| Variable | Uso |
|---|---|
| `CM_API_URL` | Dirección interna de la API (`http://api:8000`) |
| `INTERNAL_API_TOKEN` | Cabecera `X-Internal-Token` hacia Python |
| `TELEGRAM_BOT_TOKEN` | Llamadas a `api.telegram.org` |

## Importar o actualizar un workflow

```powershell
docker compose exec n8n n8n import:workflow --input=/workflows/cm-02-entrada-telegram.json
docker compose exec n8n n8n publish:workflow --id=cm02EntradaTelegram
docker compose restart n8n
```

Si editas un workflow desde la interfaz (http://127.0.0.1:5678), expórtalo y reemplaza el JSON de
esta carpeta para no perder los cambios.
