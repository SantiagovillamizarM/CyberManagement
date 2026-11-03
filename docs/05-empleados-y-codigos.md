# 05 — Empleados y códigos de registro

Código: `backend/app/services/employees.py`, `backend/app/services/registration.py`,
`backend/app/api/v1/employees.py` y `backend/app/api/internal/registration.py`.

## Código de registro

| Aspecto | Regla |
|---|---|
| Formato | `NOMBRE-XXXX-XXXX` (ej. `JHON-7KQ4-M9XT`). El prefijo sale del primer nombre, sin tildes y con máximo 6 letras. |
| Parte aleatoria | 8 caracteres Crockford Base32 (sin I, L, O, U): 40 bits ≈ 1,1 billones de combinaciones |
| Almacenamiento | Solo `HMAC-SHA256(código, REGISTRATION_CODE_PEPPER)` y una pista (`JHON-…M9XT`) |
| Visibilidad | El código completo se muestra **una sola vez**, al generarlo |
| Vigencia | 72 horas (`REGISTRATION_CODE_HOURS`) |
| Uso | Un solo uso. Generar uno nuevo revoca el anterior. |
| Tolerancia al escribir | Ignora mayúsculas, espacios y guiones; acepta O por 0 e I/L por 1 |
| Fuerza bruta | 5 intentos fallidos cada 15 minutos por cuenta de Telegram (tabla `registration_attempts`) |
| Requisitos | El empleado debe estar activo y sin Telegram vinculado |

## Resultados del canje (para el bot)

`POST /api/internal/registration/redeem` siempre responde 200 con un `status`:

| `status` | Significado | Mensaje sugerido para el bot (Fase 4) |
|---|---|---|
| `linked` | Vinculación exitosa | "¡Registro exitoso! Bienvenido, {nombre}." |
| `invalid_code` | No existe, vencido, usado o revocado | "El código no es válido o venció. Pide uno nuevo al administrador." |
| `employee_inactive` | Cuenta deshabilitada | "Tu cuenta está deshabilitada. Contacta al administrador." |
| `telegram_already_linked` | Esa cuenta de Telegram ya está vinculada | "Tu cuenta ya está vinculada, {nombre}." |
| `too_many_attempts` | Demasiados intentos fallidos | "Demasiados intentos. Espera 15 minutos." |

No se distingue entre un código inexistente y uno vencido: así no se revela información a quien prueba códigos.

## Endpoints del panel

| Método | Ruta | Rol mínimo | Descripción |
|---|---|---|---|
| `GET` | `/api/v1/employees?status=&search=&limit=&offset=` | auditor | Listar y buscar |
| `POST` | `/api/v1/employees` | admin | Crear |
| `GET` | `/api/v1/employees/{id}` | auditor | Ver detalle (con pista del código pendiente) |
| `PATCH` | `/api/v1/employees/{id}` | admin | Editar o desactivar (`status: inactive`) |
| `DELETE` | `/api/v1/employees/{id}` | admin | Eliminar (solo si no tiene registros financieros) |
| `POST` | `/api/v1/employees/{id}/registration-code` | admin | Generar código |
| `DELETE` | `/api/v1/employees/{id}/registration-code` | admin | Revocar código pendiente |
| `POST` | `/api/v1/employees/{id}/unlink-telegram` | admin | Desvincular Telegram |

## Reglas de negocio

- **Desactivar** a un empleado revoca su código pendiente. El bot rechazará sus mensajes (Fase 4).
- **Eliminar** solo se permite si el empleado no tiene gastos, viáticos ni documentos; si los tiene, la API
  responde 409 y pide desactivarlo (conservación de soportes contables).
- **Desvincular** Telegram borra la conversación en curso del bot y permite generar un código nuevo.
- Los empleados de otra empresa responden 404, como si no existieran.

## Historial de cambios

`backend/app/services/audit.py` registra en `audit_log` una fila por **cada campo que cambió**
(valor anterior, valor nuevo, quién, IP y fecha), además de las acciones `create`, `delete`,
`revoke`, `link_telegram` y `unlink_telegram`. Los campos que no cambian no se registran.
