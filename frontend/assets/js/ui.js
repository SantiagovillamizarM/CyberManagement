/** Componentes de interfaz reutilizables: notificaciones, ventanas modales y formatos. */

import { h, icon } from "./dom.js";

// ---------- Formatos (Colombia) ----------

const dateFormat = new Intl.DateTimeFormat("es-CO", { day: "2-digit", month: "short", year: "numeric", timeZone: "America/Bogota" });
const dateTimeFormat = new Intl.DateTimeFormat("es-CO", {
  day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "America/Bogota",
});
const copFormat = new Intl.NumberFormat("es-CO", { style: "currency", currency: "COP", maximumFractionDigits: 2, minimumFractionDigits: 0 });

const dayOnlyFormat = new Intl.DateTimeFormat("es-CO", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });

/**
 * Fechas "solo día" ("2026-11-03", p. ej. la fecha de expedición de una factura) NO son un
 * instante: new Date("2026-11-03") sería la medianoche UTC, que en Bogotá (UTC−5) cae el día
 * ANTERIOR. Se formatean tal cual, sin conversión de zona horaria.
 */
export function formatDate(value) {
  if (!value) return "—";
  const dayOnly = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (dayOnly) return dayOnlyFormat.format(new Date(Date.UTC(+dayOnly[1], dayOnly[2] - 1, +dayOnly[3])));
  return dateFormat.format(new Date(value));
}
export const formatDateTime = (iso) => (iso ? dateTimeFormat.format(new Date(iso)) : "—");
export const formatCOP = (value) => (value === null || value === undefined ? "—" : copFormat.format(Number(value)));

export function initials(name = "") {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0].toUpperCase()).join("") || "A";
}

export function badge(text, tone = "") {
  return h("span", { class: `badge ${tone ? `badge-${tone}` : ""}` }, text);
}

// ---------- Notificaciones ----------

let toastHost;

export function toast(message, type = "success") {
  toastHost ||= document.body.appendChild(h("div", { class: "toasts", role: "status", "aria-live": "polite" }));
  const item = h("div", { class: `toast ${type}` }, icon(type === "error" ? "alert" : "check"), h("div", {}, message));
  toastHost.append(item);
  setTimeout(() => item.remove(), type === "error" ? 6000 : 3500);
}

// ---------- Ventanas modales ----------

/**
 * Abre una ventana modal. Devuelve { close, body, footer }.
 * Se cierra con Escape, con la X o haciendo clic fuera.
 */
export function openModal({ title, content = [], actions = [], onClose } = {}) {
  const previousFocus = document.activeElement;
  const titleId = `modal-title-${Math.random().toString(36).slice(2)}`;
  const body = h("div", { class: "modal-body" }, content);
  const footer = h("div", { class: "modal-footer" }, actions);
  const closeBtn = h("button", { class: "icon-btn", type: "button", "aria-label": "Cerrar" }, icon("x"));
  const modal = h("div", { class: "modal", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId },
    h("div", { class: "modal-header" }, h("h2", { id: titleId }, title), closeBtn),
    body,
    actions.length ? footer : null,
  );
  const backdrop = h("div", { class: "modal-backdrop" }, modal);

  function close() {
    document.removeEventListener("keydown", onKey);
    backdrop.remove();
    previousFocus?.focus?.();
    onClose?.();
  }
  function onKey(event) {
    if (event.key === "Escape") close();
  }

  closeBtn.addEventListener("click", close);
  backdrop.addEventListener("mousedown", (event) => {
    if (event.target === backdrop) close();
  });
  document.addEventListener("keydown", onKey);
  document.body.append(backdrop);
  (modal.querySelector("input, select, textarea, button.btn-primary") || closeBtn).focus();
  return { close, body, footer, modal };
}

/** Pide confirmación. Resuelve true si el usuario acepta. */
export function confirmDialog({ title, message, confirmText = "Confirmar", danger = false }) {
  return new Promise((resolve) => {
    let answered = false;
    const cancel = h("button", { class: "btn btn-ghost", type: "button" }, "Cancelar");
    const accept = h("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, type: "button" }, confirmText);
    const modal = openModal({
      title,
      content: [h("p", { class: "muted" }, message)],
      actions: [cancel, accept],
      onClose: () => !answered && resolve(false),
    });
    cancel.addEventListener("click", () => modal.close());
    accept.addEventListener("click", () => {
      answered = true;
      modal.close();
      resolve(true);
    });
    accept.focus();
  });
}

/** Pone un botón en estado "cargando" mientras dura la promesa. */
export async function withLoading(button, task) {
  const original = [...button.childNodes];
  button.disabled = true;
  button.replaceChildren(h("span", { class: "spinner" }), "Procesando…");
  try {
    return await task();
  } finally {
    button.disabled = false;
    button.replaceChildren(...original);
  }
}

/** Campo de formulario con etiqueta y espacio para el mensaje de error. */
export function field({ name, label, value = "", type = "text", placeholder = "", required = false, options = null, autocomplete }) {
  const control = options
    ? h("select", { class: "select", name, id: `f-${name}` },
        options.map(([optValue, optLabel]) => h("option", { value: optValue, selected: optValue === value }, optLabel)))
    : h("input", { class: "input", name, id: `f-${name}`, type, value, placeholder, required, autocomplete });
  const error = h("div", { class: "field-error", id: `e-${name}` });
  control.setAttribute("aria-describedby", error.id);
  return h("div", { class: "field" }, h("label", { class: "label", for: control.id }, label), control, error);
}

/** Muestra los errores por campo que devolvió la API (y limpia los anteriores). */
export function showFieldErrors(form, errors) {
  for (const control of form.querySelectorAll("[name]")) {
    const message = errors[control.name] || "";
    control.setAttribute("aria-invalid", message ? "true" : "false");
    const target = form.querySelector(`#e-${control.name}`);
    if (target) target.textContent = message;
  }
}
