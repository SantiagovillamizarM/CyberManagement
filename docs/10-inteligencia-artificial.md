# 10 — Inteligencia artificial (Fase 5)

Código: `backend/app/ai/` (cliente e instrucciones) y `backend/app/services/extraction.py` (servicio).

## Idea central

**La IA propone, Python valida y el empleado confirma.** Nada de lo que responde Gemini se guarda como dato
del gasto sin pasar por las mismas reglas que se aplican a lo que escribe una persona.

```
Documento YA guardado ──► Gemini (imagen o PDF + instrucciones + esquema JSON)
                         ──► Python valida campo por campo
                         ──► ai_extractions (cada intento, también los fallidos)
                         ──► documents.processing_status
```

## Modelo

| Ajuste (`.env`) | Por defecto | Para qué |
|---|---|---|
| `GEMINI_MODEL` | `gemini-2.5-flash` | Multimodal, rápido y con nivel gratuito |
| `GEMINI_TIMEOUT_SECONDS` | 45 | Tiempo máximo de espera |
| `GEMINI_THINKING_BUDGET` | 0 | Sin "razonamiento" extra: responde en ~3 s (el empleado espera en el chat) |
| `AI_MIN_CONFIDENCE` | 0.5 | Por debajo, la factura se trata como ilegible |

- Se usa la API REST con `httpx`, sin instalar ningún SDK.
- La clave va en la cabecera `x-goog-api-key`, nunca en la URL.
- `temperature = 0`: la misma imagen da la misma respuesta.
- **Salida con esquema** (`responseSchema`): Gemini está obligado a responder un JSON con campos fijos.

## Qué se extrae

Establecimiento, NIT del vendedor, número de factura, CUFE, fecha y hora de expedición, productos (cantidad,
valor unitario y total de la línea), subtotal, impuestos, total, una categoría **de las de la empresa**, una
descripción corta y notas (propina, forma de pago…).

## Escenarios (requerimiento 3.1)

| Resultado (`ai_extractions.outcome`) | Cuándo | `documents.processing_status` |
|---|---|---|
| `extracted` → **A** | Es una factura y el total se leyó con confianza | `extracted` |
| `unreadable` → **B** | Es una factura, pero el total no se lee, es inválido o la confianza es menor a 0.5. Lo que sí se leyó se conserva | `needs_manual_input` |
| `not_invoice` → **C** | No es un comprobante de compra. No se toma ningún dato | `not_invoice` |
| `error` | Falla técnica: sin internet, demora, cuota agotada (429), respuesta bloqueada o que no es el JSON pedido | `failed` (se puede reintentar) |

## Validaciones de Python sobre lo que dice la IA

| Campo | Regla | Si no cumple |
|---|---|---|
| NIT | Dígito de verificación DIAN (`utils/nit.py`). Acepta `NIT: 900.123.456 - 8` | Se descarta y queda una advertencia |
| Fecha | `AAAA-MM-DD`, no futura y no anterior a 3 años | Se descarta |
| Montos | Entre 0 y el máximo de la BD; el total no puede ser 0 | Se descarta |
| Coherencia | Subtotal + impuestos ≈ total (tolerancia del 1 %); los productos deben sumar el total o el subtotal | La confianza baja y queda una advertencia |
| CUFE | 96 caracteres hexadecimales exactos | Se descarta (uno incompleto rompería la detección de duplicados) |
| Categoría | Debe existir en la empresa (sin importar mayúsculas) | Queda vacía |
| Textos | Se limpian los espacios y se recortan al largo de la columna | — |
| Productos | Máximo 50; sin descripción se ignoran | — |

Las advertencias quedan en `ai_extractions.parsed.warnings`.

## Seguridad

- **Instrucciones escondidas en la imagen** (*prompt injection*): las instrucciones le piden a Gemini
  ignorarlas. Además, la respuesta está atada a un esquema, se valida completa y nunca dispara acciones por sí
  misma: solo propone datos que una persona confirma.
- La clave de Gemini solo vive en Python. Los errores guardados no la incluyen.
- **Nivel gratuito:** Google puede usar los datos enviados para mejorar sus productos. Reevaluar antes de
  procesar volúmenes grandes de datos reales (Ley 1581).

## Trazabilidad (`ai_extractions`)

Cada intento guarda: modelo, versión de las instrucciones (`PROMPT_VERSION`), resultado, confianza, respuesta
original del modelo, datos validados, error, tiempo de respuesta y tokens. Si cambias las instrucciones o el
esquema, sube `PROMPT_VERSION` en `app/ai/invoice.py`.

## Endpoints del panel

| Método | Ruta | Rol | Qué hace |
|---|---|---|---|
| `POST` | `/api/v1/documents/{id}/analyze` | admin y superadmin | Lee (o vuelve a leer) la factura. Siempre responde 200 con el intento registrado, también si Gemini falló |
| `GET` | `/api/v1/documents/{id}/extractions` | todos | Historial de intentos |

El auditor no puede analizar, porque eso gasta cuota de la IA.

## Pruebas

- `tests/test_ai.py` (38 pruebas) **no llama a Gemini**: el cliente se prueba con un transporte HTTP simulado y
  el servicio con un modelo falso. Así no se gasta cuota y las pruebas funcionan sin internet.
- Prueba real (2026-11-04, `gemini-2.5-flash`):
  - Factura de restaurante generada: leyó todos los datos correctamente, incluido el NIT con DV válido, el
    INC y la categoría "Alimentación". Tardó 3,5 s.
  - Paisaje: lo clasificó como `not_invoice` en 2,4 s.

## En el bot (paso 5.2) — `app/bot/expense_flow.py`

La foto llega por `/api/internal/telegram/files` (n8n la descarga), se guarda, y **en esa misma llamada**
Python la lee con Gemini antes de responder. n8n espera hasta 120 s.

| Resultado | Lo que hace el bot |
|---|---|
| **A** — leída | "🤖 Leí tu factura." Llena los datos solos y **salta esos pasos** (`data["ai_skip"]`). Solo pregunta lo que falte (fecha o tipo de gasto si no se leyeron; nombre del establecimiento si se leyó el NIT pero no el nombre; RUT si el proveedor es nuevo) y la ubicación. El resumen dice *"Esta es la información que pude identificar en tu factura. ¿Es correcta?"* e incluye productos, subtotal e impuestos |
| **B** — ilegible | "Guardé la foto, pero no pude interpretar bien la información…". La foto queda asociada y en `needs_manual_input` (pendiente de completar), y se piden los datos a mano |
| **C** — no es factura | "No pude identificar una factura en esta imagen. ¿Quieres enviar otra fotografía?". La imagen **no** se asocia al gasto, pero queda en `documents` (`not_invoice`) con su intento en `ai_extractions` |
| Falla de Gemini | "Guardé la foto, pero en este momento no pude leerla automáticamente." Se sigue a mano; el documento queda `failed` y se puede reintentar desde el panel |

Reglas:

- **Origen del dato** (`expenses.data_source`): `ai` si se confirma tal cual; `mixed` si el empleado escribió o
  corrigió algún dato de la factura; `manual` si la IA no intervino.
- Si se corrige el **total**, los productos leídos se descartan porque ya no cuadran. Un valor unitario escrito a mano
  reemplaza los productos.
- **✏️ Editar → Foto** con una foto nueva la vuelve a leer y regresa al resumen con los datos nuevos.
- Si el NIT leído ya es de un proveedor registrado, se usa el **nombre registrado**, no el leído.
- El **RUT** nunca se envía a la IA.
- Las pruebas usan `vision=None` (IA desactivada) o un modelo falso. `tests/conftest.py` desactiva la IA en el
  cliente HTTP de pruebas, así que ninguna prueba llama a Gemini por accidente.

Prueba real (2026-11-04): conversación completa por los endpoints de n8n, con la factura de ejemplo. Gemini la
leyó y el bot solo preguntó el RUT y la ubicación. El gasto quedó con `data_source = ai`, total $68.000,
2 productos, subtotal, INC y hora.

## Pasos

- [x] 5.1 Servicio de lectura con IA, validación y registro de intentos
- [x] 5.2 El bot usa la lectura: escenarios A, B y C en la conversación
