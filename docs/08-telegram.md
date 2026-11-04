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

## Registro de gastos (paso 4.2) — `app/bot/expense_flow.py`

```
📸 Registrar gasto
 ├─ con viáticos abiertos:  [💼 viático · disponible $X] … [➕ Tenía otro dinero previo] [👛 Con mi dinero]
 └─ sin viáticos:           "¿Contabas con dinero previo para realizar estos gastos (viáticos)?" [Sí] [No]
       Sí → "¿Cuánto dinero tenías disponible inicialmente?" → ¿Para qué es? (opcional)
 → Descripción → Valor total → Valor unitario (opcional) → Fecha [Hoy] [Ayer] / dd/mm/aaaa
 → NIT (opcional, con DV) → nombre del establecimiento si es nuevo → N.º de factura (opcional)
 → Tipo de gasto [botones] → Ubicación (opcional, botón de Telegram)
 → Resumen [✅ Confirmar] [✏️ Editar] [❌ Cancelar]
```

| Regla | Detalle |
|---|---|
| Nada se guarda antes de confirmar | Las respuestas viven en `bot_sessions.data`; sobreviven a reinicios de la API |
| Todo o nada | El viático nuevo y el gasto se guardan dentro de un punto de guardado: si el gasto falla (p. ej. duplicado), tampoco queda el viático |
| Al confirmar | Gasto en estado `submitted`, con historial a nombre del **empleado** |
| Montos | `45000`, `45.000`, `$45.000`, `1.234.567,50`. Con un solo separador seguido de 3 dígitos, se toma como separador de miles (`utils/money.parse_cop`) |
| Fechas | Hoy, ayer, `dd/mm/aaaa`, `d-m-aa` o `aaaa-mm-dd`; nunca futuras (`utils/dates.parse_invoice_date`) |
| Valor unitario | Si se da, se crea una línea con cantidad = total ÷ unitario |
| Ubicación | Solo si el empleado la comparte → `location_source = telegram`. Si no, "No disponible" |
| Editar | Cualquier campo; después se vuelve al resumen |
| `/cancelar`, `/start`, `/menu` | Descartan el borrador |
| `/saldo`, `/gastos`, `/ayuda` | Responden y repiten la pregunta en curso, sin perder el progreso |
| Botones viejos | "Usa las opciones del último mensaje" + se repite la pregunta |

## Fotos y archivos (paso 4.3)

```
Empleado envía foto ─► n8n ─► Python /updates ─► [sendMessage "Recibiendo…", cm:store_file]
n8n (cm:store_file): getFile ─► descarga ─► POST /api/internal/telegram/files (multipart)
Python: guarda en documents DE INMEDIATO ─► continúa la conversación ─► acciones ─► n8n ─► Telegram
```

| Regla | Detalle |
|---|---|
| Cuándo se pide | Después de la pregunta de viáticos, con la opción "✍️ Registrar sin foto" |
| **Regla fundamental** | El archivo se guarda en cuanto llega. Si el empleado cancela o abandona, la foto queda en `documents` sin gasto |
| Antes de descargar | Más de 10 MB o un tipo distinto de JPG/PNG/WEBP/PDF → se rechaza sin descargar |
| Después de descargar | Se valida el contenido real (ver `docs/07-archivos.md`); si no sirve, se avisa y se vuelve a pedir |
| GPS | Telegram borra el EXIF de las "fotos": el bot sugiere enviarla como **archivo**. Si trae GPS y el empleado no compartió su ubicación → `location_source = exif` |
| Foto desde el menú | Se guarda y se inicia el registro; la pregunta de viáticos se hace igual y ya no se vuelve a pedir la foto |
| Foto de otro gasto | Se avisa y no se asocia (probable factura duplicada) |
| Segunda foto en el mismo registro | Se responde "Ya tengo la foto"; se reemplaza con ✏️ Editar → Foto |
| RUT | Solo para proveedores nuevos, opcional. Se guarda como documento `rut` y se asocia al proveedor al confirmar |

`cm:store_file` es una **acción de transporte**, no un método de Telegram. n8n la reconoce y descarga el
archivo; la decisión de qué hacer con él la toma Python.

## Consulta y corrección de gastos (paso 4.4) — `app/bot/fix_flow.py`

```
🧾 Mis gastos ─► [03/11 · Almuerzo · $45.000 · enviado] … (últimos 10; 🔒 = ya no se puede modificar)
   ─► detalle (con el motivo si fue rechazado) ─► [✏️ Corregir] [📤 Reenviar] [↩️ Mis gastos]
        ─► Descripción · Valor total · Fecha · NIT · N.º de factura · Tipo de gasto · 📸 Agregar foto
```

| Regla | Detalle |
|---|---|
| Se guarda al instante | Cada corrección llama a `services.expenses.update_expense`: mismas reglas que el panel |
| Quién puede | Solo el dueño del gasto (uno ajeno se trata como inexistente) y mientras no esté aprobado, cerrado ni en un viático cerrado |
| Historial | Campo por campo, con el empleado como autor |
| Edición simultánea | Si el admin lo modificó mientras tanto → aviso y se muestra la versión actual; nada se pisa |
| Duplicados | Si la corrección deja una factura repetida → aviso y no cambia nada |
| Reenviar | Para gastos rechazados (o borradores): vuelven a `submitted` y se borra el motivo del rechazo |
| Fotos | "Agregar foto" asocia una foto nueva; las anteriores se conservan |
| Validaciones | Las mismas del registro, en `app/bot/inputs.py` (compartido) |

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
