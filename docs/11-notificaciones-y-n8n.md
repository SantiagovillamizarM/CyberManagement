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

Pendiente.
