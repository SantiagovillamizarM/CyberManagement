# 09 — Panel web

Código: `frontend/`. HTML, CSS y JavaScript (módulos ES), sin frameworks ni proceso de compilación.

## Cómo se sirve

- **Desarrollo:** la propia API lo sirve en http://127.0.0.1:8000, en el mismo origen que `/api`. Las cookies
  de sesión funcionan sin configurar CORS. `docker-compose.yml` monta `./frontend` en el contenedor.
- **Producción (Fase 10):** lo servirá Caddy directamente.
- La API tiene prioridad: el panel se monta **al final** (`app.mount("/")`), y toda ruta registrada después queda
  tapada por él.

## Estructura

```
frontend/
├── index.html · login.html · empleados.html · gastos.html   (solo cargan su módulo JS)
└── assets/
    ├── css/   tokens.css (colores, fuente y medidas), base.css, components.css, layout.css
    ├── js/    dom.js (construcción segura), api.js (cliente), ui.js (modales, avisos, formatos),
    │          layout.js (menú y barra superior), scene.js (escena de la portada), pages/*.js
    ├── fonts/ archivo-variable.woff2 + licencia OFL
    └── img/   favicon.svg
```

## Identidad visual: "La luz es el dato verificado"

> Decisión del 2026-11-04: el **azul eléctrico** reemplaza al *midnight purple* del requerimiento 11.

El azul eléctrico no decora: **se enciende donde hay información confirmada o una acción importante**.
Eso es el botón principal, la sección activa del menú, el indicador "Gastos por revisar" (solo si hay
pendientes), el código de registro y la escena de la portada. Todo lo demás queda en una oscuridad tranquila.

| Token | Hex | Uso |
|---|---|---|
| `--abyss` | `#05070d` | Fondo: casi negro con un toque azul |
| `--night` | `#0a1122` | Paneles y ventanas |
| `--navy` / `--line` | `#16233f` / `#19264a` | Superficies elevadas y bordes finos |
| `--electric` | `#2f6bff` | Color protagonista (resplandor en `#4d8dff`) |
| `--text` | `#f4f7ff` | Texto principal |
| `--text-muted` | `#8794b2` | Texto secundario (gris azulado) |

- **Tipografía:** una sola familia, **Archivo** (variable, OFL), alojada en `assets/fonts/`. Los títulos van en
  ancho *Expanded* (`font-stretch: 125%`) y peso 760–800; el texto, en ancho normal. Los montos usan números
  tabulares (`.num`) y van alineados a la derecha.
- **Esquinas con jerarquía:** 8 px en controles, 14 px en paneles, 18 px en ventanas y pastillas solo para estados.
- **Sin** etiquetas en mayúsculas, separadores con punto medio, degradados de relleno ni animaciones de entrada por
  sección.
- Todos los valores están en `assets/css/tokens.css`.

### Movimiento fluido (120/144 Hz)

- **Solo se animan `transform` y `opacity`**: la GPU los compone sin repintar, así el movimiento va a la
  frecuencia de la pantalla.
- **Cambio de página:** View Transitions del navegador (`@view-transition` en `base.css`). La página anterior
  se queda en pantalla hasta que la nueva está lista, y el menú y la barra superior quedan fijos. En navegadores
  sin soporte, `motion.js` (`leaveTo`) hace una salida propia.
- `blocking="render"` en el script de cada página, más el menú dibujado al instante desde `sessionStorage`
  (solo el nombre y el rol): no hay instante en blanco.
- Curvas `--ease-out`, `--ease-in-out` y `--ease-spring` en `tokens.css`. Las ventanas y avisos entran y salen
  animados; mientras se cierran quedan inertes y sueltan sus `id`.

### La portada: carrusel de funciones y fondo vivo

`assets/js/carousel.js` + `assets/css/login.css`. Cuatro escenas en SVG (`scene.js`), 6,5 s cada una:

| Escena | Qué cuenta |
|---|---|
| Escanea facturas | El haz recorre la factura y cada dato queda verificado |
| Guarda cada dato | Los datos viajan a la base de datos ("Guardado con historial") |
| Viáticos al día | Medidor de lo gastado contra lo entregado |
| Todo por Telegram | La conversación con el bot, de la foto a "Gasto registrado" |

- El tiempo lo marca la barra del punto activo (animación CSS). Al pasar el mouse se pausa.
- Con movimiento reducido no avanza solo, y cada escena aparece ya resuelta.
- Detrás, un fondo discreto: luces que derivan, dos órbitas, un barrido de luz y chispas que suben. Sus
  posiciones vienen de clases CSS (`.sp1…8`, `.vs-c1…3`), porque **la CSP bloquea `style=""` en línea**.
- Los textos solo afirman lo que el sistema hace. Por ejemplo, no se dice "cifrado": la BD no está cifrada en reposo.

### Dashboard (`pages/dashboard.js`, `charts.js`, `dashboard.css`)

Gráficos en SVG propio, sin librerías, siguiendo el skill dataviz:

| Bloque | Forma |
|---|---|
| Gastado en el mes | Cifra principal que cuenta hasta su valor, con su variación frente al mes anterior |
| Gasto diario (30 días) | Área de una serie, con cruz de lectura (mouse y flechas del teclado) |
| Indicadores | Franja; "Gastos por revisar" se enciende solo si hay pendientes |
| Facturas por estado | Barra segmentada delgada + lista con la cantidad y el monto de cada estado |
| Gasto por categoría | Barras horizontales ordenadas, de un solo tono, con el valor en la punta |
| Viáticos abiertos | Medidores: acento → advertencia (≥ 85 %) → peligro (> 100 %), con ícono y texto |

- **Colores de estado validados** con `validate_palette.js` sobre `#0a1122` (todas las comprobaciones pasan):
  aprobado `#199e70`, enviado `#2f6bff`, pendiente `#c98500`, en revisión `#9085e9`, rechazado `#e66767`.
  Cada estado tiene el mismo color en los gráficos y en las insignias.
- Cada gráfico tiene una **tabla equivalente** para lectores de pantalla, y los tooltips usan `textContent`.
- Hoy se agregan hasta 200 gastos en el navegador. Con más volumen, un endpoint de estadísticas en el backend
  hará esos cálculos (paso 7.4).

### El momento de la portada: el escaneo de la factura

`assets/js/scene.js` dibuja una factura con líneas de luz (SVG) y `layout.css` (`.rc-*`) la anima una sola vez:
un haz azul la recorre a velocidad constante y, a su paso, cada renglón borroso se convierte en un dato nítido.
Los datos son NIT con dígito de verificación válido, fecha de hoy, productos, subtotal e IVA. Al final, el total
se enciende y aparece el sello **Verificado**. Dura unos 3 segundos.

- El tiempo de cada renglón sigue la fórmula `retraso = 0,35 s + 2,7 s × (y + 30) / 482`. Si se mueve un renglón,
  hay que recalcular su retraso.
- Con `prefers-reduced-motion` la factura aparece ya verificada y quieta.
- Dentro del panel, el movimiento solo responde a lo que hace la persona: abrir ventanas y mostrar avisos.

Responsive: por debajo de 960 px el login apila la escena sobre el formulario; por debajo de 900 px el menú se
convierte en panel deslizable; por debajo de 760 px las tablas se muestran como tarjetas.

## Facturas y gastos (paso 7.2) — `gastos.html`, `pages/gastos.js`, `css/expenses.css`

| Parte | Qué hace |
|---|---|
| Filtros | Por estado (*Todos*, *Por revisar* = enviado + en revisión, *Aprobados*, *Rechazados*, *Por completar*, *Cerrados*), búsqueda libre (descripción, establecimiento, NIT o n.º de factura), empleado, tipo de gasto y rango de fechas de la factura. 50 por página, con "Cargar más" |
| Detalle | La **foto a la izquierda** (clic: tamaño completo en otra pestaña; un PDF se abre aparte) y los **datos a la derecha**: establecimiento, factura, fechas de expedición y de recepción, pago, ubicación (enlace al mapa con su origen), productos y advertencias de coherencia |
| Lectura con IA | Tabla **"Leído por IA" frente a "Registrado"**: lo que el empleado o el administrador corrigió aparece marcado como *Corregido*. Muestra la confianza, y el botón **Volver a leer** llama a Gemini de nuevo |
| Historial | Quién hizo qué y cuándo (administrador, empleado o sistema), con valor anterior → nuevo y motivo de rechazo |
| Acciones | Según el estado: *Empezar revisión*, **Aprobar**, **Rechazar** (motivo obligatorio), *Cerrar*, *Enviar a revisión*, *Corregir* (con bloqueo optimista: si otra persona lo cambió, se avisa) y *Eliminar*. El auditor solo ve |
| Avisos | Al aprobar o rechazar, el empleado recibe el aviso por Telegram (bandeja de salida → CM-06) |
| Fotos sin registrar | Fotos que llegaron por el bot pero nunca quedaron en un gasto (por ejemplo, el empleado no terminó). Las que no eran factura no se muestran |
| Enlaces | `gastos.html?id=<uuid>` abre el detalle directo, y `?status=review` abre la lista en "Por revisar". El dashboard usa ambos |

Backend agregado para esta pantalla:

- `GET /api/v1/expenses?search=…&status=submitted&status=in_review`: búsqueda libre y varios estados a la vez.
- `GET /api/v1/expenses/{id}/history`: el historial, con el nombre de quién hizo cada cambio.
- `GET /api/v1/documents?unattached=true`: fotos sin gasto.
- La advertencia "la suma de los productos no coincide" ahora acepta que sumen el **subtotal o el total**, porque en
  Colombia los precios suelen incluir el IVA.

## Seguridad

| Medida | Dónde |
|---|---|
| **Anti-XSS:** los datos se insertan siempre como texto (`textContent`), nunca como HTML | `dom.js` (`h()`, `mount()`) |
| **CSP:** solo scripts y estilos propios, sin scripts en línea, sin iframes | `PANEL_CSP` en `backend/app/main.py` |
| **CSRF:** las peticiones que modifican datos envían `X-CSRF-Token` (cookie `cm_csrf`) | `api.js` |
| La sesión vive en una cookie `HttpOnly` que el JS no puede leer | backend (paso 3.2) |
| Sesión vencida (401) → al login, conservando la página | `api.js` |
| El `next=` del login solo acepta rutas internas (evita redirecciones a otros sitios) | `pages/login.js` |
| El código de registro se muestra una sola vez | `pages/empleados.js` |
| Las fechas "solo día" (`2026-11-03`) se muestran sin conversión de zona horaria (antes caían un día antes en Bogotá) | `ui.js` (`formatDate`) |

## Pruebas

- `backend/tests/test_panel_static.py`: el panel se sirve con CSP, no tiene scripts en línea y no expone
  archivos fuera de `frontend/` (por ejemplo, `.env`).
- `backend/tests_e2e/`: pruebas en **Edge real** con Playwright (necesitan la API corriendo):
  ```powershell
  cd backend
  .venv\Scripts\python.exe -m pytest tests_e2e -v
  ```
  Usan la base de desarrollo y borran todo lo que crean (empleados `E2E-*`, sus gastos, fotos y archivos).
  `wait_for_entrance(page)` espera a que termine la animación de entrada antes de medir posiciones; esa era la causa
  de la prueba intermitente del dashboard.

## Secciones

| Sección | Estado |
|---|---|
| Login | ✅ 7.1 |
| Dashboard (indicadores básicos y gastos recientes) | ✅ 7.1 (los gráficos llegan en el 7.4) |
| Empleados: crear, editar, activar o desactivar, eliminar, generar o revocar código, desvincular Telegram | ✅ 7.1 |
| Facturas y gastos: revisar con la foto, comparar con la IA, aprobar, rechazar, corregir e historial | ✅ 7.2 |
| Viáticos | 7.3 |
| Dashboard con gráficos | 7.4 |
| Informes PDF | Fase 8 |
