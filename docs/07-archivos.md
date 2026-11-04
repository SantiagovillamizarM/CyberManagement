# 07 — Archivos (fotos de facturas, RUT y PDF)

Código: `backend/app/storage/` (almacenamiento e inspección), `backend/app/services/documents.py`,
`backend/app/api/v1/documents.py` y `backend/app/api/internal/documents.py`.

## Regla fundamental

La foto se guarda **antes** de cualquier análisis con IA y existe por sí misma en `documents`,
aunque todavía no tenga un gasto (`expense_id = null`). Si la IA falla, la foto no se pierde.

## Dónde se guardan

- En disco, en el volumen `./storage` (en el contenedor, `/data/storage`). **No** en PostgreSQL.
- Ruta: `{company_id}/{año}/{mes}/{document_id}.{ext}`. El nombre original **nunca** forma parte de
  la ruta; solo se guarda como dato informativo.
- La escritura es atómica (archivo temporal + renombrado): nunca queda un archivo a medias.
- Doble defensa contra *path traversal*: la clave debe cumplir un formato estricto y la ruta final
  debe quedar dentro de la carpeta raíz.
- `app/storage/base.py` define el contrato. Cambiar a S3 o R2 en el futuro solo requiere escribir otra
  implementación.

## Validación

| Regla | Resultado |
|---|---|
| Tipo detectado por el **contenido**, no por la extensión | JPEG, PNG, WEBP o PDF; cualquier otro → 415 |
| Archivo vacío | 422 |
| Más de 10 MB (`MAX_UPLOAD_MB`) | 413. Se leen como máximo 10 MB + 1 byte: nunca entra completo a memoria. |
| Imagen corrupta o truncada | 422. Se decodifica completa, no solo la cabecera. |
| Bomba de descompresión (más de 50 MP, `MAX_IMAGE_PIXELS`) | 422 |

> El PDF hoy solo se valida por su firma (`%PDF-`). Se revisará cuando se procesen PDF con IA (Fase 5).

### Decisión: se guarda el original intacto

En la Fase 1 se propuso volver a codificar las imágenes. Se decidió **conservar el archivo original**:
es el soporte contable y su SHA-256 demuestra que no fue alterado. La protección contra contenido
malicioso se logra así:

- validación del contenido real;
- `Content-Type` fijo, tomado de la validación y no del cliente, junto con `X-Content-Type-Options: nosniff`;
- `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox` en las imágenes;
- descarga solo con sesión, y nombre de archivo = ID del documento.

Las miniaturas para el panel se generarán aparte (Fase 7).

## Duplicados

Se calcula el SHA-256 de cada archivo; `UNIQUE (company_id, sha256)`.

| Situación | Respuesta |
|---|---|
| Se sube un archivo que ya existe | 200, con `duplicate: true` y el documento existente; no se guarda otra copia |
| La misma foto se adjunta a **otro** gasto | 409, con `existing_expense_id` (probable factura duplicada) |

## Ubicación desde el EXIF

- Del EXIF se guarda solo un resumen: marca, modelo, fecha original y GPS. Nunca el bloque completo.
- El GPS se convierte de grados, minutos y segundos a decimal: Sur y Oeste son negativos.
- Sin GPS → `gps: null`. **Nunca se inventa.**
- Al adjuntar una foto con GPS a un gasto **sin** ubicación, se completa con `location_source = exif`.
  Una ubicación existente nunca se sobrescribe.
- Telegram elimina el EXIF de las imágenes enviadas como "foto". Solo se conserva si se envía como
  "archivo" (ver la Fase 4).

## Coherencia disco ↔ base de datos

1. Primero se escribe el archivo. Si falla, no se crea ningún registro.
2. Luego se crea el registro. Si falla, se borra el archivo: no quedan archivos huérfanos.

## Retención

`retention_until = fecha de recepción + companies.retention_years` (10 años por defecto,
Código de Comercio, art. 60).

## Endpoints

| Método | Ruta | Acceso |
|---|---|---|
| `GET` | `/api/v1/documents?employee_id=&expense_id=&processing_status=&kind=` | auditor |
| `GET` | `/api/v1/documents/{id}` | auditor |
| `GET` | `/api/v1/documents/{id}/file` | auditor (se muestra en el navegador) |
| `POST` | `/api/v1/expenses/{id}/documents` (multipart: `file`, `kind`) | admin + CSRF |
| `POST` | `/api/internal/documents` (multipart: `file`, `telegram_user_id`, `telegram_file_unique_id`) | solo n8n; el empleado debe estar vinculado y activo |
