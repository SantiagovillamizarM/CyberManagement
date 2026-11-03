# 03 — Diagrama entidad-relación y modelo relacional

Refleja la migración `e03f1d92d5f6` (esquema inicial). Fuente de verdad: `backend/app/models/`.

> Los diagramas usan [Mermaid](https://mermaid.js.org/). GitHub los dibuja automáticamente;
> en VS Code se ven con la extensión *Markdown Preview Mermaid Support*.

## 1. Vista general (entidades y relaciones)

```mermaid
erDiagram
    companies ||--o{ admin_users : "tiene"
    companies ||--o{ employees : "emplea"
    companies ||--o{ categories : "define"
    companies ||--o{ suppliers : "registra"
    companies ||--o{ expenses : "contabiliza"
    companies ||--o{ documents : "almacena"
    companies ||--o{ audit_log : "audita"
    companies ||--o{ reports : "genera"

    admin_users ||--o{ admin_sessions : "inicia"
    admin_users |o--o{ registration_codes : "crea"
    admin_users |o--o{ viatico_movements : "registra"
    admin_users |o--o{ viaticos : "cierra"
    admin_users |o--o{ expenses : "revisa"
    admin_users |o--o{ reports : "solicita"

    employees ||--o{ registration_codes : "recibe"
    employees ||--o{ data_consents : "acepta"
    employees ||--o{ viaticos : "recibe"
    employees ||--o{ expenses : "registra"
    employees |o--o{ documents : "envía"
    employees |o--o{ bot_sessions : "conversa"
    employees |o--o{ telegram_events : "genera"

    viaticos ||--o{ viatico_movements : "acumula"
    viaticos |o--o{ expenses : "financia"

    categories |o--o{ expenses : "clasifica"
    suppliers |o--o{ expenses : "factura"
    suppliers |o--o{ documents : "RUT de"

    expenses ||--o{ expense_items : "detalla"
    expenses |o--o{ documents : "soporta"
    expenses |o--o{ bot_sessions : "borrador de"

    documents ||--o{ ai_extractions : "analizado en"
```

**Cómo leer las líneas** (notación "pata de gallo"):

| Símbolo | Significado |
|---|---|
| `\|\|` | exactamente uno (obligatorio) |
| `\|o` | cero o uno (opcional) |
| `o{` | cero o muchos |

Ejemplo: `viaticos |o--o{ expenses` significa que un gasto pertenece a **cero o un** viático
(puede pagarse con dinero propio), y un viático tiene **cero o muchos** gastos.

## 2. Diagrama detallado

```mermaid
erDiagram
    companies {
        uuid id PK
        varchar name
        varchar nit
        varchar timezone
        int retention_years
    }
    admin_users {
        uuid id PK
        uuid company_id FK
        varchar email UK
        varchar password_hash "Argon2id"
        varchar role "superadmin | admin | auditor"
        bool is_active
        int failed_login_attempts
        timestamptz locked_until
    }
    admin_sessions {
        uuid id PK
        uuid admin_user_id FK
        varchar token_hash UK "SHA-256"
        varchar csrf_token_hash
        timestamptz expires_at
        timestamptz revoked_at
    }
    employees {
        uuid id PK
        uuid company_id FK
        varchar first_name
        varchar last_name
        varchar internal_id "UK con company_id"
        varchar status "active | inactive"
        bigint telegram_user_id UK
        bigint telegram_chat_id
        timestamptz linked_at
        timestamptz deleted_at
    }
    registration_codes {
        uuid id PK
        uuid employee_id FK
        varchar code_hash UK "HMAC-SHA256"
        varchar code_hint
        uuid created_by_id FK
        timestamptz expires_at
        timestamptz used_at
        timestamptz revoked_at
    }
    data_consents {
        uuid id PK
        uuid employee_id FK
        varchar policy_version
        varchar channel
        timestamptz accepted_at
    }
    viaticos {
        uuid id PK
        uuid employee_id FK
        varchar purpose
        varchar status "open | closed"
        timestamptz opened_at
        timestamptz closed_at
        uuid closed_by_id FK
    }
    viatico_movements {
        uuid id PK
        uuid viatico_id FK
        varchar type "initial | additional | refund | adjustment"
        numeric amount "14,2"
        varchar source "employee | admin"
        uuid created_by_id FK
    }
    categories {
        uuid id PK
        uuid company_id FK
        varchar name "UK con company_id"
        bool is_active
    }
    suppliers {
        uuid id PK
        uuid company_id FK
        varchar nit "UK con company_id"
        varchar dv
        varchar name
    }
    expenses {
        uuid id PK
        uuid company_id FK
        uuid employee_id FK
        uuid viatico_id FK "opcional"
        uuid category_id FK
        uuid supplier_id FK
        varchar invoice_number
        varchar cufe "UK parcial"
        date issue_date "expedición"
        time issue_time
        timestamptz received_at "recepción"
        numeric subtotal
        numeric tax_total
        numeric total
        varchar payment_source
        bool is_reimbursable
        varchar location_source
        varchar status
        varchar data_source "ai | manual | mixed"
        int version "bloqueo optimista"
    }
    expense_items {
        uuid id PK
        uuid expense_id FK
        int position
        varchar description
        numeric quantity
        numeric unit_price
        numeric line_total
    }
    documents {
        uuid id PK
        uuid company_id FK
        uuid employee_id FK
        uuid expense_id FK "opcional"
        uuid supplier_id FK "opcional"
        varchar kind "invoice_photo | rut | other"
        varchar processing_status
        varchar storage_key UK
        varchar sha256 "UK con company_id"
        jsonb exif
    }
    ai_extractions {
        uuid id PK
        uuid document_id FK
        varchar model
        varchar prompt_version
        varchar outcome
        bool is_invoice
        numeric confidence
        jsonb raw_response
        jsonb parsed
    }
    bot_sessions {
        bigint telegram_user_id PK
        uuid employee_id FK
        varchar state
        jsonb data
        uuid draft_expense_id FK
    }
    telegram_events {
        bigint id PK
        bigint update_id UK
        bigint telegram_user_id
        uuid employee_id FK
        varchar event_type
    }
    audit_log {
        bigint id PK
        uuid company_id FK
        varchar entity_type
        varchar entity_id
        varchar field
        jsonb old_value
        jsonb new_value
        varchar actor_type
        varchar actor_id
    }
    reports {
        uuid id PK
        uuid company_id FK
        uuid generated_by_id FK
        jsonb filters
        varchar storage_key UK
    }

    companies ||--o{ admin_users : ""
    companies ||--o{ employees : ""
    companies ||--o{ categories : ""
    companies ||--o{ suppliers : ""
    companies ||--o{ expenses : ""
    companies ||--o{ documents : ""
    companies ||--o{ audit_log : ""
    companies ||--o{ reports : ""
    admin_users ||--o{ admin_sessions : ""
    employees ||--o{ registration_codes : ""
    employees ||--o{ data_consents : ""
    employees ||--o{ viaticos : ""
    employees ||--o{ expenses : ""
    employees |o--o{ documents : ""
    employees |o--o{ bot_sessions : ""
    employees |o--o{ telegram_events : ""
    viaticos ||--o{ viatico_movements : ""
    viaticos |o--o{ expenses : ""
    categories |o--o{ expenses : ""
    suppliers |o--o{ expenses : ""
    suppliers |o--o{ documents : ""
    expenses ||--o{ expense_items : ""
    expenses |o--o{ documents : ""
    expenses |o--o{ bot_sessions : ""
    documents ||--o{ ai_extractions : ""
```

Para no saturar el diagrama detallado, las relaciones de auditoría hacia `admin_users`
(`created_by_id`, `closed_by_id`, `reviewed_by_id`, `generated_by_id`) solo aparecen en la vista general.

## 3. Modelo relacional

Notación: **PK** clave primaria · **FK** → tabla referenciada · **UK** valor único · `?` admite nulos.
Todas las tablas con `created_at`/`updated_at` los llenan automáticamente con la hora del servidor.

### Empresa y administración

**companies** (**id** PK, name, nit?, timezone, retention_years, created_at, updated_at)

**admin_users** (**id** PK, company_id FK→companies, email UK, full_name, password_hash, role,
is_active, last_login_at?, failed_login_attempts, locked_until?, created_at, updated_at)

**admin_sessions** (**id** PK, admin_user_id FK→admin_users *ON DELETE CASCADE*, token_hash UK,
csrf_token_hash, created_at, expires_at, last_seen_at, ip_address?, user_agent?, revoked_at?)

### Empleados

**employees** (**id** PK, company_id FK→companies, first_name, last_name, internal_id, status,
telegram_user_id? UK, telegram_chat_id?, telegram_username?, linked_at?, deleted_at?, created_at, updated_at)
- UK (company_id, internal_id)

**registration_codes** (**id** PK, employee_id FK→employees *CASCADE*, code_hash UK, code_hint,
created_by_id? FK→admin_users, created_at, expires_at, used_at?, used_by_telegram_user_id?, revoked_at?)
- UK parcial (employee_id) WHERE used_at IS NULL AND revoked_at IS NULL

**data_consents** (**id** PK, employee_id FK→employees *CASCADE*, policy_version, channel, accepted_at)

### Viáticos

**viaticos** (**id** PK, employee_id FK→employees, purpose?, status, opened_at, closed_at?,
closed_by_id? FK→admin_users, created_at, updated_at)

**viatico_movements** (**id** PK, viatico_id FK→viaticos *CASCADE*, type, amount, source, note?,
created_by_id? FK→admin_users, created_at)
- CHECK amount <> 0 AND (amount > 0 OR type = 'adjustment')

### Gastos

**categories** (**id** PK, company_id FK→companies, name, is_active)
- UK (company_id, name)

**suppliers** (**id** PK, company_id FK→companies, nit, dv?, name, created_at, updated_at)
- UK (company_id, nit)

**expenses** (**id** PK, company_id FK→companies, employee_id FK→employees, viatico_id? FK→viaticos,
category_id? FK→categories, supplier_id? FK→suppliers, description?, invoice_number?, cufe?,
issue_date?, issue_time?, subtotal?, tax_total?, total?, currency, received_at, payment_source?,
is_reimbursable, location_lat?, location_lon?, location_text?, location_source, status, data_source,
rejection_reason?, reviewed_by_id? FK→admin_users, reviewed_at?, deleted_at?, version, created_at, updated_at)
- CHECK total IS NULL OR total >= 0
- UK parcial (company_id, cufe) WHERE cufe IS NOT NULL AND deleted_at IS NULL
- UK parcial (company_id, supplier_id, invoice_number) WHERE ambos IS NOT NULL AND deleted_at IS NULL

**expense_items** (**id** PK, expense_id FK→expenses *CASCADE*, position, description, quantity,
unit_price?, line_total?)
- CHECK quantity > 0

### Archivos e IA

**documents** (**id** PK, company_id FK→companies, employee_id? FK→employees,
expense_id? FK→expenses *ON DELETE SET NULL*, supplier_id? FK→suppliers, kind, processing_status,
storage_key UK, mime_type, size_bytes, sha256, width?, height?, original_filename?,
telegram_file_unique_id?, exif?, received_at, retention_until?, deleted_at?, created_at, updated_at)
- UK (company_id, sha256)

**ai_extractions** (**id** PK, document_id FK→documents *CASCADE*, model, prompt_version, outcome,
is_invoice?, confidence?, raw_response?, parsed?, error?, latency_ms?, tokens_in?, tokens_out?, created_at)

### Telegram

**bot_sessions** (**telegram_user_id** PK, employee_id? FK→employees *CASCADE*, state, data,
draft_expense_id? FK→expenses *SET NULL*, updated_at, expires_at?)

**telegram_events** (**id** PK, update_id UK, telegram_user_id, employee_id? FK→employees *SET NULL*,
event_type, received_at)

### Trazabilidad

**audit_log** (**id** PK, company_id FK→companies, entity_type, entity_id, action, field?, old_value?,
new_value?, actor_type, actor_id?, ip_address?, reason?, created_at)
- Índice (entity_type, entity_id). Sin FK hacia la entidad: es **polimórfica** (registra cambios
  de cualquier tabla) y debe conservarse aunque el registro original se archive.

**reports** (**id** PK, company_id FK→companies, generated_by_id? FK→admin_users, filters,
storage_key UK, created_at)

## 4. Decisiones de diseño reflejadas en el modelo

| Decisión | Por qué |
|---|---|
| `documents.expense_id` es opcional | Una foto se guarda **antes** de que exista el gasto, y una que no es factura nunca lo tendrá. |
| `expenses.viatico_id` es opcional | Si el empleado pagó con su dinero, no hay viático (`is_reimbursable = true`). |
| El saldo no es una columna | Se calcula con `viatico_movements` y `expenses`; así no puede desincronizarse. |
| Fecha de expedición ≠ fecha de recepción | `issue_date`/`issue_time` vienen de la factura; `received_at`, de Telegram. |
| `ON DELETE CASCADE` solo en tablas "hijas" puras | Líneas de factura, sesiones o intentos de IA no tienen sentido sin su padre. Gastos, empleados y documentos usan borrado lógico. |
| `bot_sessions` usa `telegram_user_id` como PK | La conversación existe incluso antes de que el usuario se vincule como empleado. |
