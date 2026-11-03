# 02 — Base de datos

Definida en `backend/app/models/`; la estructura la crean las migraciones de Alembic
(`backend/alembic/versions/`). Nunca se crean tablas a mano.

## Convenciones

- **Claves primarias UUID** (no 1, 2, 3…), para que no se puedan adivinar registros desde la URL.
  Excepción: `audit_log` y `telegram_events` usan `BIGINT` porque tienen mucho volumen y nunca se exponen.
- **Dinero:** `NUMERIC(14,2)` y `Decimal` en Python. Nunca `float`.
- **Fechas:** `timestamptz`, guardadas en UTC y mostradas en `America/Bogota`.
- **Estados:** texto con una restricción `CHECK`. La base rechaza cualquier valor que no esté en la lista,
  aunque alguien escriba SQL a mano.
- **Borrado lógico** (`deleted_at`) en empleados, gastos y documentos, por la conservación contable.

## Tablas

| Área | Tablas |
|---|---|
| Empresa y administración | `companies`, `admin_users`, `admin_sessions` |
| Empleados | `employees`, `registration_codes`, `data_consents` |
| Viáticos | `viaticos`, `viatico_movements` |
| Gastos | `expenses`, `expense_items`, `categories`, `suppliers` |
| Archivos e IA | `documents`, `ai_extractions` |
| Telegram | `bot_sessions`, `telegram_events` |
| Trazabilidad | `audit_log`, `reports` |

## Dos estados distintos

- `documents.processing_status`: qué pasó con la **imagen** en la IA
  (`received` → `analyzing` → `extracted` | `needs_manual_input` | `not_invoice` | `failed`).
- `expenses.status`: el ciclo de vida del **gasto**
  (`draft` → `pending_completion` → `submitted` → `in_review` → `approved` | `rejected` → `closed`).

Una foto que no es factura (escenario C) queda en `documents` con su intento en `ai_extractions`,
pero **sin** gasto asociado.

## Reglas que protege la base de datos

| Regla | Mecanismo |
|---|---|
| Una cuenta de Telegram por empleado | `UNIQUE (telegram_user_id)` |
| Un solo código vigente por empleado | Índice único parcial (`used_at IS NULL AND revoked_at IS NULL`) |
| Factura electrónica no duplicada | Índice único parcial en (`company_id`, `cufe`) |
| Factura no duplicada | Índice único parcial en (`company_id`, `supplier_id`, `invoice_number`) |
| Imagen no duplicada | `UNIQUE (company_id, sha256)` |
| Totales no negativos | `CHECK` |
| Movimientos de viático: solo los ajustes pueden ser negativos | `CHECK` |
| Ediciones simultáneas | Columna `version` (bloqueo optimista) |

## Saldo del viático

No se guarda: se calcula cada vez.

```
saldo = Σ viatico_movements.amount − Σ expenses.total (gastos del viático no rechazados ni borrados)
```

Cada regla está cubierta por una prueba en `backend/tests/test_models.py`.
