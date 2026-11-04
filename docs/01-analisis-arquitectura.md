# 01 — Análisis y arquitectura (Fase 1, aprobada el 2026-11-03)

## Decisiones aprobadas

| Tema | Decisión |
|---|---|
| Empresas | Una sola empresa; el modelo incluye `company_id` para crecer. |
| IA | Gemini Flash, **nivel gratuito**. Si no reconoce la factura, el empleado la digita. |
| Almacenamiento de imágenes | Archivos en disco (volumen de Docker) + referencia y SHA-256 en PostgreSQL. No se usa BYTEA. |
| Respaldo externo | No por ahora; respaldos locales con `pg_dump`. |
| Hosting | El usuario tiene dominio/hosting con Plesk; viabilidad a evaluar en la Fase 10. |
| Líneas de factura | Tabla `expense_items` (descripción, cantidad, valor unitario, total de línea). |
| Viáticos | La pregunta "¿Contabas con dinero previo?" solo se hace si no hay viático abierto. Un empleado puede tener **varios viáticos abiertos**. |
| Código de registro | Formato `NOMBRE-XXXX-XXXX` (Crockford base32), un solo uso, 72 h de vigencia, guardado como HMAC. |
| Categoría | Gemini la sugiere y el empleado la confirma. |
| Frontend | HTML + CSS + JS (módulos ES) + Chart.js, sin frameworks. |
| PDF | WeasyPrint + Jinja2, dentro de Docker. |

## Principio de comunicación

- n8n **coordina**: recibe los mensajes de Telegram, descarga archivos, llama a Python y envía respuestas.
- Python **decide**: reglas de negocio, validaciones, permisos, estado de la conversación y persistencia.
- Python responde a n8n con una lista de **acciones** (`send_message`, `trigger`, …).
- El token de Telegram solo vive en n8n; la clave de Gemini solo vive en Python.
- Telegram admite un solo webhook por bot, por lo que el WF2 (router) es la única entrada.

```
Telegram ──webhook──► n8n (WF2 router → WF1/WF3/WF4/WF5/WF6/WF7)
                        │  HTTP interno + X-Internal-Token
                        ▼
                     FastAPI ──► PostgreSQL · Storage · Gemini
                        ▲
Navegador ──HTTPS──► Caddy (/ → frontend, /api → FastAPI)
```

## Regla fundamental

La foto se guarda **antes** de llamar a la IA. Ninguna falla de Gemini puede hacer perder una
factura válida.

## Consideraciones conocidas

- **Gemini gratuito:** según los términos de Google, en el nivel gratuito los datos enviados
  pueden usarse para mejorar sus productos. Reevaluar antes de procesar volúmenes grandes de
  datos reales (Ley 1581 de 2012).
- **EXIF:** Telegram elimina los metadatos (incluido el GPS) de las imágenes enviadas como
  "foto". Solo se conservan si se envían como "archivo".
- **Windows:** usar `127.0.0.1` y no `localhost` para conectarse a PostgreSQL desde el equipo
  (con `localhost`, Python intenta primero IPv6 y la conexión se cuelga).
- Conservación contable: retención configurable (por defecto, 10 años).

## Estado de las fases

- [x] Fase 1 — Análisis y arquitectura
- [x] Fase 2 — Configuración inicial (verificada el 2026-11-03: PostgreSQL, API, n8n, Gemini y Telegram)
- [x] Fase 3 — Backend (completada el 2026-11-03)
  - [x] 3.1 Modelos y migraciones (ver [02-base-de-datos.md](02-base-de-datos.md))
  - [x] 3.2 Seguridad y login del administrador (ver [04-seguridad.md](04-seguridad.md))
  - [x] 3.3 Empleados y códigos de registro (ver [05-empleados-y-codigos.md](05-empleados-y-codigos.md))
  - [x] 3.4 Viáticos, gastos y auditoría (ver [06-viaticos-y-gastos.md](06-viaticos-y-gastos.md))
  - [x] 3.5 Archivos (ver [07-archivos.md](07-archivos.md))
- [ ] Fase 4 — Telegram
  - [ ] 4.1 Conexión, motor de conversación y vinculación (código listo; falta la prueba con Telegram real)
  - [ ] 4.2 Registro de gastos conversacional
  - [ ] 4.3 Fotos y archivos desde Telegram
- [ ] Fase 5 — Inteligencia artificial
- [ ] Fase 6 — n8n
- [ ] Fase 7 — Panel web
- [ ] Fase 8 — Informes
- [ ] Fase 9 — Pruebas y seguridad
- [ ] Fase 10 — Despliegue
