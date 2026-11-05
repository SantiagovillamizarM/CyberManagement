# 11 — Notificaciones y workflows de n8n (Fase 6)

## Avisos por Telegram (paso 6.1)

Código: `backend/app/services/notifications.py`, `backend/app/models/notification.py`,
`backend/app/api/internal/notifications.py`. Textos en `backend/app/bot/messages.py` (`NOTIF_*`).

### Bandeja de salida ("transactional outbox")

```
aprobar un gasto + crear su aviso ──(misma transacción)──► notifications (pending)
n8n, cada minuto ──► POST /api/internal/notifications/claim ──► Telegram
                 ──► POST /api/internal/notifications/{id}/result
```

¿Por qué no se envía directamente? Python no tiene el token del bot (solo lo tiene n8n). Además, con la bandeja:

- **Nunca se avisa algo que no pasó:** si el cambio se deshace, el aviso se deshace con él.
- **Nunca se pierde un aviso:** si n8n o Telegram están caídos, el aviso espera.
- **Nunca se envía dos veces:** `claim` bloquea las filas con `FOR UPDATE SKIP LOCKED`. Se probó con dos conexiones
  reales a la vez: cada una tomó avisos distintos.

### Qué se avisa

| Evento | A quién | Cuándo |
|---|---|---|
| Gasto **aprobado** | Empleado | Al aprobar (`expenses.approve`) |
| Gasto **rechazado**, con el motivo | Empleado | Al rechazar (`expenses.reject`) |
| Viático **asignado** | Empleado | Solo si lo abre el administrador. Si lo declaró el empleado en el bot, no |
| Dinero **adicional**, **reintegro** o **ajuste** del viático | Empleado | Solo si lo registra el administrador. Incluye el saldo disponible |
| Viático **cerrado** | Empleado | Con lo entregado, lo gastado, lo reintegrado y la liquidación (debe reintegrar, se le debe o paz y salvo) |
| **Recordatorio:** registro de gasto sin terminar | Empleado | Conversación `exp:*` sin actividad por más de `REMINDER_IDLE_HOURS` (12 h) |
| **Recordatorio:** fotos de facturas sin registrar | Empleado | Guardadas hace más de 12 h y menos de 7 días, sin gasto. No incluye las que no eran factura |
| **Resumen:** gastos por revisar | Administrador | Cantidad y total, si hay alguno |

- Los recordatorios salen **una vez por día como máximo** (`dedup_key` único). Se pueden ejecutar varias veces sin
  repetirse.
- No se avisa a empleados sin Telegram vinculado ni desactivados.
- Todo dato escrito por personas se escapa (`html.escape`) antes de enviarse con `parse_mode=HTML`.

### Avisos al administrador

`ADMIN_TELEGRAM_CHAT_ID` en el `.env`: el chat (o grupo) donde llegan. Para conocer el número de tu chat, escríbele a
`@userinfobot`. Si está vacío, no se crean avisos para el administrador.

### Reintentos

| Intento | Espera antes del siguiente |
|---|---|
| 1 → 2 | 1 min |
| 2 → 3 | 5 min |
| 3 → 4 | 15 min |
| 4 → 5 | 60 min |
| 5 | Se marca `failed` |

- Errores **permanentes** (`403`: el empleado bloqueó el bot; `400 chat not found`) → `failed` de inmediato, sin
  insistir.
- Si n8n toma un aviso y no reporta en 5 minutos (por ejemplo, porque se cayó a mitad), se vuelve a ofrecer.
- Reportar dos veces el mismo resultado no cambia nada (idempotente).

### Endpoints internos (`X-Internal-Token`)

| Método | Ruta | Lo llama |
|---|---|---|
| `POST` | `/api/internal/notifications/claim?limit=20` | CM-06, cada minuto |
| `POST` | `/api/internal/notifications/{id}/result` `{"ok", "error_code", "description"}` | CM-06, por cada aviso |
| `POST` | `/api/internal/reminders/run` | CM-08, una vez al día |

`claim` devuelve cada aviso como una **acción** (`{"method": "sendMessage", "payload": {...}}`), en el mismo formato
del bot. Así n8n los ejecuta con el mismo nodo.

### Pruebas

`tests/test_notifications.py` (22 pruebas). Se inyectaron 7 fallas en el código y todas fueron detectadas.

## Workflows de n8n (paso 6.2)

Detalle completo en [n8n/README.md](../n8n/README.md). Se importan con `.\infra\n8n-import.ps1`.

| Requerimiento 9 | Dónde quedó |
|---|---|
| WF1 Registro de empleados | CM-02 → Python (`bot/engine.py`: autorización de datos, código, vinculación) |
| WF2 Registro de gastos | CM-02 (entrada y clasificación: mensaje o archivo) → Python (`bot/expense_flow.py`) |
| WF3 Análisis de facturas con IA | CM-03 (obtiene la imagen) → Python (`services/extraction.py`: Gemini, validación, datos faltantes) |
| WF4 Registro manual | Python (`bot/expense_flow.py`: pide, valida y confirma), con las acciones ejecutadas por CM-05 |
| WF5 Control de viáticos | Python (`services/viaticos.py`); los avisos de saldo salen por CM-06 |
| WF6 Notificaciones | CM-06 (entrega), CM-08 (recordatorios) y CM-99 (errores al administrador) |
| WF7 Informes | Fase 8 |

Las reglas de negocio quedan en Python, como pide el requerimiento. n8n coordina: recibe, descarga, entrega, programa y
alerta.

### Prueba en vivo (2026-11-04, n8n 2.46.4)

Se usaron chats inexistentes para no escribirle a nadie, y se revisó el historial de ejecuciones de n8n. 12 de 12
comprobaciones pasaron:

- Webhook con un secreto falso: Python nunca recibe el mensaje.
- Mensaje normal: CM-02 → Python → CM-05. Telegram rechaza el chat falso y CM-99 alerta **una** vez.
- Python falla (422): CM-02 avisa al empleado, se detiene con error y CM-99 alerta.
- Foto con un `file_id` inválido: CM-03 avisa *"No pude recibir tu archivo"* y CM-99 alerta. El mensaje "Recibiendo…"
  falló antes, pero eso **no impidió** que el archivo se intentara procesar.
- CM-06: un aviso a un chat inexistente terminó en `failed` con `400 Bad Request: chat not found`, sin reintentos.
- CM-08 ejecutado a mano: Python devolvió los conteos.
- CM-99 oculta el token del bot (`bot***`) en los mensajes de error.

Hallazgos corregidos durante la prueba:

1. Una falla en un sub-workflow generaba **dos** alertas.
2. Si fallaba el mensaje "Recibiendo…", **la foto no se procesaba**. Eso violaba la regla fundamental.
