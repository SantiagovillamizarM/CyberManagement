# 12 — Informes PDF (Fase 8)

Código: `backend/app/services/reports.py` (datos), `backend/app/reports/pdf.py` (documento) y
`backend/app/api/v1/reports.py` (endpoints).

## Librería: ReportLab

Es Python puro: funciona igual en Windows y en Docker, sin instalar librerías del sistema. Reemplaza a WeasyPrint
(decisión del 2026-11-04), que necesitaba GTK/Pango. Versión fijada: `reportlab==4.4.10`. Las pruebas leen el texto
del PDF con `pypdf`, que es solo de desarrollo.

## Filtros

| Filtro | Detalle |
|---|---|
| Fecha inicial y final | Obligatorias. Máximo un año por informe; la final no puede ser anterior a la inicial |
| Empleado y tipo de gasto | Opcionales |
| Estados | Vacío = lo que cuenta como gastado (enviado, en revisión, aprobado y cerrado) |
| Fotos | `include_photos` (sí por defecto) |

Mismas reglas que el dashboard: el día de un gasto es la fecha de la factura o, si falta, la de recepción en hora de
Colombia.

## Contenido del PDF (requerimiento 8)

1. **Encabezado en cada hoja:** nombre de la empresa y período. Pie con "Generado el … por …" y "Página N de M".
2. **Resumen general:** facturas, total, subtotal e impuestos. Si no todas las facturas traen subtotal, la etiqueta dice
   de cuántas sale la suma (por ejemplo, "Subtotal (1 de 6 facturas)"). También los viáticos del período: cuántos, total
   entregado, gastado y **saldo restante**.
3. **Desgloses:** por estado, por tipo de gasto y por empleado.
4. **Viáticos del período:** los vigentes en algún momento del período, con entregado, gastado, saldo y liquidación
   (*Debe reintegrar*, *La empresa le debe* o *Paz y salvo*).
5. **Gastos individuales:** fecha, empleado, establecimiento con NIT y descripción, factura, tipo, estado y total. La
   columna *Foto* remite al anexo (A1, A2…). El encabezado de la tabla se repite en cada hoja.
6. **Anexos:** las fotos de las facturas, dos por fila, cada una con su referencia. Se reducen a 1.300 px en JPEG
   (calidad 72) y respetan la orientación EXIF; el original queda intacto en el sistema. Máximo 60 fotos: si hay más,
   se avisa. Una factura en PDF o una foto ilegible deja un aviso y el informe se genera igual.

## Seguridad

- **Textos escapados:** ReportLab interpreta etiquetas tipo HTML (`<b>`, `<font>`) dentro del texto. Todo lo que
  escribe una persona pasa por `escape()`. Hay una prueba con `<font size="60">` en la descripción.
- **Vista previa:** el endpoint del PDF responde con `X-Frame-Options: SAMEORIGIN` y `frame-ancestors 'self'`. Solo el
  propio panel puede mostrarlo en un iframe; el resto del sitio sigue en `DENY`.
- Cada informe queda en la tabla `reports` (filtros, resumen, tamaño y quién lo generó) y en la **auditoría**.
- Límites: un año por informe, 2.000 facturas y 60 fotos, para que ningún informe tumbe el servidor.
- El auditor puede generar informes (son de solo lectura).

## Endpoints

| Método | Ruta | Qué hace |
|---|---|---|
| `POST` | `/api/v1/reports` | Genera y guarda el informe; devuelve su resumen y `file_url` |
| `GET` | `/api/v1/reports` | Los últimos informes generados |
| `GET` | `/api/v1/reports/{id}/file` | El PDF para vista previa (`inline`); con `?download=true` se descarga |

## Página del panel (paso 8.2) — `informes.html`, `pages/informes.js`, `css/reports.css`

| Parte | Qué hace |
|---|---|
| Filtros | Atajos (*Este mes*, *Mes anterior*, *Últimos 30 días* y *Este año*, en hora de Colombia), fechas, empleado, tipo de gasto, estados (por defecto los que cuentan como gastado) e incluir fotos |
| Validación | Fecha final anterior a la inicial o ningún estado elegido: se explica sin llamar al servidor. Los errores del servidor se muestran tal cual, en español |
| Vista previa | El PDF aparece en un iframe dentro de la página (`#view=FitH` lo ajusta al ancho). **No se descarga solo**: hay una prueba que confirma que no hubo ninguna descarga |
| Acciones | **Descargar PDF** (`?download=true`) y **Abrir en otra pestaña** (útil en celular, donde algunos navegadores no muestran PDF dentro de la página). Resumen con facturas, total, fotos y peso |
| Historial | Los últimos 20 informes: cuándo, período, filtros, quién, facturas y total, con *Ver* y *Descargar*. `informes.html?id=<id>` abre uno directamente |
| Roles | El auditor también genera informes |

## Informe mensual automático (paso 8.3, WF7 = CM-07)

```
CM-07 (día 1, 7:00 a. m.) ─► POST /api/internal/reports/monthly      Python genera el del mes anterior
                          ─► GET  /api/internal/reports/{id}/file     n8n descarga el PDF
                          ─► Telegram sendDocument                    al chat del administrador
                          ─► POST /api/internal/reports/{id}/delivery n8n informa si llegó
```

| Regla | Detalle |
|---|---|
| Período | El mes anterior **completo**, del día 1 al último día (incluido el 29 de febrero en años bisiestos), con los estados por defecto |
| Nunca se duplica | Si ya existe el informe de ese mes se reutiliza. Un **índice único** en la base (`uq_reports_monthly`, por empresa y mes) lo garantiza aunque dos ejecuciones corran a la vez |
| Nunca se reenvía | Si ya se entregó (`summary.telegram_sent_at`), n8n no lo vuelve a mandar. Si falló, el error queda en `summary.telegram_error` y la siguiente ejecución lo intenta de nuevo |
| Sin chat | Sin `ADMIN_TELEGRAM_CHAT_ID`, Python responde `skipped`: no genera nada y n8n termina sin error |
| Mensaje | "📊 Informe de gastos de octubre de 2026", la empresa, las facturas, el total y el saldo de viáticos. El nombre de la empresa se escapa |
| Fallas | Si Telegram rechaza el PDF, CM-07 falla a propósito para que CM-99 alerte |
| En el panel | Aparece en "Informes anteriores" como generado por "Envío automático mensual" |

Para recibirlo, configura `ADMIN_TELEGRAM_CHAT_ID` en el `.env` y ejecuta `docker compose up -d api n8n`.

## Pruebas

`tests/test_reports.py` (21 pruebas), `tests/test_reports_monthly.py` (11) y `tests_e2e/test_panel_reports.py` (7 en Edge real). Se inyectaron 6 fallas y todas fueron detectadas: texto sin escapar, rechazados
por defecto, sin fecha final, iframe permitido en todo el sitio, sin auditoría e informe de otra empresa.

Prueba en vivo (2026-11-04): con los datos reales de desarrollo generó un PDF de 2 páginas con la foto real de la
factura. Ese informe de prueba se borró después.

Prueba en vivo del 8.3 (2026-11-04):

- **Sin chat configurado:** CM-07 terminó bien con `skipped` y no se creó ningún informe.
- **Con un chat falso temporal (`-1`):** Python generó el informe de octubre, n8n descargó el PDF y lo envió con
  `sendDocument`. Telegram respondió `400 chat not found`, el error quedó guardado en el informe y CM-07 terminó en
  error a propósito. Después se borró el informe y se restauró la configuración.
