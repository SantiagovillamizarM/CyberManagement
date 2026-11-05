# Workflows de n8n

Los workflows se guardan aquí en JSON y se versionan con Git. **No contienen reglas de negocio**: reciben,
llaman a Python y ejecutan las acciones que Python devuelve. Python decide qué responder, qué guardar, qué avisar
y a quién.

| Archivo | Workflow | Disparador | Qué hace |
|---|---|---|---|
| `cm-02-entrada-telegram.json` | CM-02 · Entrada Telegram | Webhook de Telegram | Verifica el secreto, envía el update a Python y reparte las acciones: mensajes a CM-05, archivos a CM-03 |
| `cm-03-archivos-ia.json` | CM-03 · Archivos e IA | Sub-workflow | Descarga el archivo de Telegram y lo sube a Python, que lo guarda y lo lee con Gemini. Las acciones resultantes van a CM-05 |
| `cm-05-ejecutar-acciones.json` | CM-05 · Ejecutar acciones | Sub-workflow | Ejecuta cada acción en la Bot API. Ignora errores inofensivos y reporta los demás |
| `cm-06-notificaciones.json` | CM-06 · Notificaciones | Cada minuto (y a mano) | Toma la bandeja de salida de Python, envía los avisos e informa el resultado de cada uno |
| `cm-08-recordatorios.json` | CM-08 · Recordatorios | 8:00 a. m. (y a mano) | Le pide a Python crear los recordatorios del día. Se envían por CM-06 |
| `cm-99-errores.json` | CM-99 · Errores | Falla de otro workflow | Alerta al administrador por Telegram, con el token del bot oculto |

## Cómo se relacionan

```
Telegram ─► CM-02 ─(secreto ok)─► Python /updates
              ├─ mensajes ─► CM-05 ─► Telegram
              └─ archivos ─► CM-03 ─► getFile ─► descarga ─► Python /files (guarda + Gemini) ─► CM-05
CM-06 (cada minuto) ─► /notifications/claim ─► Telegram ─► /notifications/{id}/result
CM-08 (8:00 a. m.)  ─► /reminders/run
CM-99 ◄── CM-02, CM-05, CM-06 y CM-08 cuando fallan
```

## Manejo de errores

| Situación | Qué pasa |
|---|---|
| Llega algo al webhook sin el secreto correcto | Se descarta en silencio. Nadie puede usar el bot para escribirle a otros |
| Python falla con un mensaje | El empleado recibe *"⚠️ Tuve un problema procesando tu mensaje…"* y CM-99 alerta |
| Falla la descarga o el guardado de un archivo | El empleado recibe *"⚠️ No pude recibir tu archivo. Envíalo de nuevo…"* y CM-99 alerta |
| Telegram rechaza un mensaje (salvo errores inofensivos) | CM-05 alerta. **El archivo se procesa igual**: un mensaje fallido nunca hace perder una foto |
| Errores inofensivos ("message is not modified", botón vencido, bot bloqueado) | Se ignoran |
| Telegram rechaza un aviso de CM-06 | Python decide: reintentar más tarde o no insistir |

- **Una alerta por falla:** CM-05 y CM-03 son sub-workflows. CM-05 reporta sus propias fallas y quien lo llama sigue
  adelante; las de CM-03 las reporta CM-02.
- Las ejecuciones exitosas de CM-06 **no se guardan**, porque corre 1.440 veces al día. Las fallidas sí.
- Sin `ADMIN_TELEGRAM_CHAT_ID`, CM-99 no envía nada; las fallas quedan en el historial de ejecuciones de n8n.

## Variables de entorno (definidas en `docker-compose.yml`)

| Variable | Uso |
|---|---|
| `CM_API_URL` | Dirección interna de la API (`http://api:8000`) |
| `INTERNAL_API_TOKEN` | Cabecera `X-Internal-Token` hacia Python |
| `TELEGRAM_BOT_TOKEN` | Llamadas a `api.telegram.org` |
| `TELEGRAM_WEBHOOK_SECRET` | CM-02 descarta lo que no lo traiga |
| `ADMIN_TELEGRAM_CHAT_ID` | A dónde envía CM-99 las alertas (opcional) |

## Importar o actualizar los workflows

Con n8n encendido, desde la raíz del proyecto:

```powershell
.\infra\n8n-import.ps1
```

El script importa y publica los 6 workflows en el orden correcto y reinicia n8n. Cada workflow tiene un ID fijo, así
que importar de nuevo **reemplaza** la versión anterior; no crea copias.

Si editas un workflow desde la interfaz (http://127.0.0.1:5678), expórtalo y reemplaza el JSON de esta carpeta para no
perder los cambios.

Para ejecutar CM-08 a mano desde la terminal (con n8n encendido se necesita otro puerto para el *task runner*):

```powershell
docker compose exec -e N8N_RUNNERS_BROKER_PORT=5690 n8n n8n execute --id=cm08Recordatorios
```
