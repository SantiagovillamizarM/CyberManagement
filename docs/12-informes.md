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

## Pruebas

`tests/test_reports.py` (21 pruebas). Se inyectaron 6 fallas y todas fueron detectadas: texto sin escapar, rechazados
por defecto, sin fecha final, iframe permitido en todo el sitio, sin auditoría e informe de otra empresa.

Prueba en vivo (2026-11-04): con los datos reales de desarrollo generó un PDF de 2 páginas con la foto real de la
factura. Ese informe de prueba se borró después.
