/**
 * Cliente de la API.
 *
 * - La sesión viaja sola en la cookie HttpOnly "cm_session" (el JS no puede leerla).
 * - Toda petición que modifica datos envía la cabecera X-CSRF-Token, leída de la
 *   cookie "cm_csrf" (esa sí es legible a propósito).
 * - Si la sesión venció (401), se redirige al login conservando la página actual.
 */

const BASE = "/api/v1";
const SAFE_METHODS = new Set(["GET", "HEAD"]);

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.status = status;
    this.detail = detail;
  }

  /** Errores de validación por campo: { first_name: "mensaje", ... } */
  get fieldErrors() {
    if (!Array.isArray(this.detail)) return {};
    const errors = {};
    for (const item of this.detail) {
      const field = item.loc?.[item.loc.length - 1];
      if (field && !errors[field]) errors[field] = translateValidation(item);
    }
    return errors;
  }
}

function csrfToken() {
  const match = document.cookie.match(/(?:^|;\s*)cm_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

/** Traduce los mensajes de validación de la API (Pydantic) a español. */
function translateValidation(item) {
  const ctx = item.ctx || {};
  switch (item.type) {
    case "missing":
      return "Este campo es obligatorio";
    case "string_too_short":
      return ctx.min_length > 1 ? `Debe tener al menos ${ctx.min_length} caracteres` : "Este campo es obligatorio";
    case "string_too_long":
      return `Máximo ${ctx.max_length} caracteres`;
    case "string_pattern_mismatch":
      return "Usa solo letras, números, puntos, guiones y guiones bajos (sin espacios)";
    default:
      return "Revisa este campo";
  }
}

function messageFrom(status, body) {
  const detail = body?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail) && detail.message) return detail.message;
  if (Array.isArray(detail)) return "Revisa los datos marcados.";
  if (status === 403) return "No tienes permiso para esta acción.";
  if (status >= 500) return "Ocurrió un error en el servidor. Intenta de nuevo.";
  return "No se pudo completar la acción.";
}

export async function api(path, { method = "GET", body } = {}) {
  const headers = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!SAFE_METHODS.has(method)) headers["X-CSRF-Token"] = csrfToken();

  let response;
  try {
    response = await fetch(BASE + path, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
    });
  } catch {
    throw new ApiError(0, "No hay conexión con el servidor.");
  }

  if (response.status === 401 && !path.startsWith("/auth/login")) {
    try { sessionStorage.removeItem("cm_admin"); } catch { /* sin almacenamiento */ }
    const next = encodeURIComponent(location.pathname + location.search);
    location.replace(`/login.html?next=${next}`);
    throw new ApiError(401, "Sesión expirada");
  }

  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new ApiError(response.status, messageFrom(response.status, data), data?.detail);
  return data;
}

/** Construye un query string ignorando los valores vacíos. */
export function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value !== null && value !== undefined) search.set(key, value);
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}
