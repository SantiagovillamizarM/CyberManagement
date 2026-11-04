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
├── index.html · login.html · empleados.html     (solo cargan su módulo JS)
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
  Usan la base de desarrollo y borran todo lo que crean (empleados `E2E-*`).

## Secciones

| Sección | Estado |
|---|---|
| Login | ✅ 7.1 |
| Dashboard (indicadores básicos y gastos recientes) | ✅ 7.1 (los gráficos llegan en el 7.4) |
| Empleados: crear, editar, activar o desactivar, eliminar, generar o revocar código, desvincular Telegram | ✅ 7.1 |
| Facturas y gastos | 7.2 |
| Viáticos | 7.3 |
| Dashboard con gráficos | 7.4 |
| Informes PDF | Fase 8 |
