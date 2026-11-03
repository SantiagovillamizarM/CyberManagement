# 04 — Seguridad del panel de administración

Código: `backend/app/core/security.py`, `backend/app/services/auth.py`, `backend/app/api/deps.py`.

## Contraseñas

- Hash **Argon2id** (`argon2-cffi`). Nunca se guarda la contraseña.
- Cada hash lleva una sal aleatoria: dos contraseñas iguales producen hashes distintos.
- Si los parámetros de Argon2 se endurecen en el futuro, el hash se actualiza solo en el siguiente login.
- Longitud mínima: 12 caracteres (`MIN_PASSWORD_LENGTH`).

## Login

| Protección | Detalle |
|---|---|
| Mensaje genérico | Siempre "Correo o contraseña incorrectos", exista o no el correo, esté bloqueada o desactivada la cuenta. |
| Tiempo constante | Si el correo no existe, igual se verifica contra un hash ficticio, para que la respuesta no tarde menos. |
| Bloqueo | 5 intentos fallidos → cuenta bloqueada 15 minutos (`MAX_FAILED_LOGINS`, `LOCKOUT_MINUTES`). |
| Correo | Se normaliza a minúsculas y sin espacios. |

> Pendiente para producción (Fase 10): límite de peticiones por IP en Caddy, y `--proxy-headers`
> en Uvicorn para registrar la IP real del cliente detrás del proxy.

## Sesiones

- Token aleatorio de 256 bits en la cookie `cm_session`, con `HttpOnly`, `SameSite=Strict`,
  `Path=/api` y `Secure` en producción.
- En la base de datos solo se guarda su **hash SHA-256**: un respaldo robado no sirve para entrar.
- Duración máxima: 8 horas. Se cierra tras 60 minutos sin actividad.
- Logout, desactivar al administrador o el vencimiento invalidan la sesión de inmediato en el servidor.

## CSRF

Toda petición que modifica datos (`POST`, `PUT`, `PATCH`, `DELETE`) debe enviar la cabecera
`X-CSRF-Token`. El panel recibe ese token al iniciar sesión, en la respuesta y en la cookie `cm_csrf`,
que sí es legible por JavaScript. Otra página web puede hacer que el navegador envíe la cookie de sesión,
pero no puede leer el token CSRF.

## Roles

| Rol | Uso previsto |
|---|---|
| `superadmin` | Todo, incluida la gestión de otros administradores |
| `admin` | Operación diaria: empleados, gastos, viáticos, informes |
| `auditor` | Solo lectura |

En los endpoints: `Depends(require_roles(AdminRole.SUPERADMIN, AdminRole.ADMIN))`.

## Cabeceras HTTP

`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY` y `Referrer-Policy: same-origin`
en todas las respuestas. Además, `Cache-Control: no-store` en `/api/v1/*`.
CSP y HSTS se configuran en la Fase 7 (frontend) y en la Fase 10 (Caddy).

## Administrador inicial

```powershell
cd backend
.venv\Scripts\python.exe -m scripts.create_admin
```

Lee `COMPANY_NAME`, `ADMIN_EMAIL` y `ADMIN_INITIAL_PASSWORD` del `.env`. Si el administrador ya
existe, no cambia nada: nunca sobrescribe una contraseña.
