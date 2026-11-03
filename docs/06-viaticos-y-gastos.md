# 06 — Viáticos y gastos

Código: `backend/app/services/viaticos.py`, `backend/app/services/expenses.py`,
`backend/app/services/catalog.py`, `backend/app/utils/nit.py`.

## Viáticos

El dinero **entregado** al empleado (movimientos) es distinto del dinero **gastado** (facturas).

| Movimiento | Signo | Regla |
|---|---|---|
| `initial` | + | Solo al abrir el viático |
| `additional` | + | Dinero adicional recibido |
| `refund` | − | Reintegro del empleado a la empresa |
| `adjustment` | ± | Corrección; exige una nota |

```
entregado = inicial + adicionales ± ajustes
gastado   = Σ total de gastos enviados, en revisión, aprobados o cerrados
restante  = entregado − reintegrado − gastado
```

No cuentan como gastado: borradores, pendientes de completar, rechazados ni eliminados.

| `settlement` | Significado |
|---|---|
| `employee_must_return` | Le sobró dinero: debe reintegrarlo |
| `company_must_reimburse` | Gastó más de lo entregado: la empresa le debe |
| `balanced` | A paz y salvo |

- Un empleado puede tener **varios viáticos abiertos** a la vez.
- Un viático **cerrado** no acepta movimientos ni gastos nuevos.
- No se puede cerrar si tiene gastos sin aprobar ni rechazar.
- El administrador puede corregir los gastos de un viático cerrado (queda en el historial); el empleado no,
  y tampoco se puede reenviar un gasto rechazado de un viático cerrado.

## Gastos

### Ciclo de vida

```
draft / pending_completion ──submit──► submitted ──start-review──► in_review
rejected ──────────submit──► submitted
submitted / in_review ──approve──► approved ──close──► closed
submitted / in_review ──reject (motivo)──► rejected
```

Para enviar (`submit`) son obligatorios: total mayor que 0, descripción y fecha de expedición.

### Quién puede editar

| Estado | Empleado (bot, Fase 4) | Administrador |
|---|---|---|
| draft, pending_completion, submitted, in_review, rejected | Sí, solo sus propios gastos | Sí |
| approved, closed | No | Sí, con historial |

Cada edición exige la `version` que se estaba viendo. Si otra persona guardó antes, la respuesta es
**409** y no se pisa ningún cambio (bloqueo optimista).

### Validaciones

| Regla | Tipo |
|---|---|
| NIT con dígito de verificación DIAN (módulo 11). Sin DV, se calcula. | Bloquea (422) |
| Fecha de expedición futura (hora de Bogotá) | Bloquea (422) |
| Latitud sin longitud o viceversa | Bloquea (422) |
| Viático de otro empleado o cerrado | Bloquea (422) |
| CUFE repetido, o mismo NIT + número de factura | Bloquea (409), con el ID del gasto existente |
| Subtotal + impuestos ≠ total (más de $1 de diferencia) | Solo aviso |
| Suma de las líneas ≠ subtotal | Solo aviso |
| Sin NIT del establecimiento | Solo aviso |

- Pagado con dinero propio (`own_money`) → `is_reimbursable = true`.
- Con viático → `payment_source = viatico` automáticamente.
- Sin ubicación → `location_source = none` ("No disponible"). Nunca se inventa.
- Si la IA extrajo los datos y una persona los corrige → `data_source = mixed`.
- Los montos viajan en JSON como texto (`"59500.00"`) para no perder precisión.

### Historial

Cada campo modificado se guarda en `audit_log` con su valor anterior y nuevo. Esto incluye el NIT del
proveedor (`supplier_nit`), las líneas de factura (`items`, como lista completa antes y después) y
cada cambio de estado, junto con el motivo del rechazo.

## Endpoints

| Método | Ruta | Rol |
|---|---|---|
| `GET` / `POST` | `/api/v1/categories` | auditor / admin |
| `GET` / `POST` | `/api/v1/viaticos` (`?employee_id=&status=`) | auditor / admin |
| `GET` | `/api/v1/viaticos/{id}` (incluye saldo y movimientos) | auditor |
| `POST` | `/api/v1/viaticos/{id}/movements` | admin |
| `POST` | `/api/v1/viaticos/{id}/close` | admin |
| `GET` | `/api/v1/expenses` (filtros abajo) | auditor |
| `POST` | `/api/v1/expenses` | admin |
| `GET` / `PATCH` / `DELETE` | `/api/v1/expenses/{id}` | auditor / admin / admin |
| `POST` | `/api/v1/expenses/{id}/submit`, `start-review`, `approve`, `reject`, `close` | admin |

Filtros de `GET /api/v1/expenses`: `employee_id`, `status`, `issue_date_from`, `issue_date_to`,
`supplier_nit`, `establishment`, `min_total`, `max_total`, `invoice_number`, `category_id`, `viatico_id`,
`limit`, `offset`.

## Categorías por defecto

Alimentación, Transporte, Hospedaje, Combustible, Peajes, Parqueadero, Papelería y Otros.
Se crean con `python -m scripts.create_admin`.
