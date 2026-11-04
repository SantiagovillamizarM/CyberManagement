# 08 — Bot de Telegram

Código: `backend/app/bot/` (motor, textos, acciones), `backend/app/api/internal/telegram.py`,
`n8n/workflows/cm-02-entrada-telegram.json`.

## Cómo viaja un mensaje

```
Telegram ──HTTPS + secret_token──► túnel (cloudflared) ──► n8n /webhook/telegram
   n8n ──X-Internal-Token + X-Telegram-Secret──► POST /api/internal/telegram/updates (Python)
   Python ──{"actions": [...]}──► n8n ──► api.telegram.org/bot<TOKEN>/<method> (una por acción)
```

- **Python decide todo**: el estado de la conversación (`bot_sessions`), las reglas, los permisos y los textos.
- **n8n solo transporta**: no tiene ningún `if` de negocio.
- **El token del bot** solo lo conoce n8n (variable de entorno). Python valida el `secret_token`.

## Contrato de acciones

```json
{"actions": [
  {"method": "sendMessage", "payload": {"chat_id": 123, "text": "...", "parse_mode": "HTML",
                                         "reply_markup": {"inline_keyboard": [[{"text": "✅ Acepto", "callback_data": "consent:yes"}]]}}},
  {"method": "answerCallbackQuery", "payload": {"callback_query_id": "..."}},
  {"method": "editMessageReplyMarkup", "payload": {"chat_id": 123, "message_id": 55, "reply_markup": {"inline_keyboard": []}}}
]}
```

Cada `method` es un método oficial de la Bot API. Para agregar capacidades nuevas no hay que tocar n8n.

## Conversación (paso 4.1)

| Estado | Entrada | Respuesta | Nuevo estado |
|---|---|---|---|
| `idle` (no vinculado) | cualquier mensaje | Bienvenida + autorización de datos | `awaiting_consent` |
| `awaiting_consent` | ✅ Acepto | Pide el código | `awaiting_code` |
| `awaiting_consent` | ❌ No acepto | "Sin tu autorización no podemos registrarte" | `idle` |
| `awaiting_consent` | texto (incluso un código) | "Responde con los botones" | (igual) |
| `awaiting_code` | código válido | "¡Registro exitoso! Bienvenido, {nombre}" + menú | `menu` |
| `awaiting_code` | código inválido o vencido | Mensaje de error; puede reintentar | (igual) |
| vinculado | `/start`, `/menu`, texto | Menú principal | `menu` |
| vinculado | `/saldo` o 💰 | Viáticos abiertos con su saldo | `menu` |
| vinculado | `/gastos` o 🧾 | Últimos 5 gastos | `menu` |
| cualquiera | `/cancelar`, `/ayuda` | Cancela o muestra la ayuda | — |
| empleado desactivado | cualquier mensaje | "Tu cuenta está deshabilitada" | `idle` |

- La **autorización (Ley 1581)** se pide **antes** del código y se guarda en `data_consents` con la
  versión de la política (`DATA_POLICY_VERSION`) al quedar vinculado.
- **Idempotencia:** cada `update_id` se registra en `telegram_events`; si Telegram reenvía el mismo mensaje, se ignora.
- Solo se atienden **chats privados**: los grupos se ignoran.
- Todo dato del usuario pasa por `html.escape` antes de insertarse en los mensajes.
- Cuando se pulsa un botón se responde el callback y **se quitan los botones** de ese mensaje, para que no se pulsen dos veces.

> ⚠️ **Pendiente de la empresa:** el texto de la autorización (`app/bot/messages.py`) es un resumen.
> Debe enlazar la política oficial de tratamiento de datos, revisada por un abogado. Si el texto cambia,
> hay que subir `DATA_POLICY_VERSION`.

## Puesta en marcha en desarrollo

1. Docker encendido y `docker compose up -d`.
2. Abrir el túnel y registrar el webhook (la URL cambia en cada reinicio del túnel):
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\infra\dev-tunnel.ps1
   ```
3. Revisar el estado:
   ```powershell
   cd backend
   .venv\Scripts\python.exe -m scripts.telegram_webhook info
   ```
4. Cerrar el túnel cuando no se use:
   ```powershell
   docker compose --profile tunnel stop tunnel
   ```

> Mientras el túnel está abierto, n8n es accesible desde internet en la URL `trycloudflare.com`.
> Ciérralo cuando termines de probar.
