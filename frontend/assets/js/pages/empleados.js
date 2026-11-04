import { api, ApiError, query } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { canEdit, renderShell, startPage } from "../layout.js";
import {
  badge, confirmDialog, field, formatDate, formatDateTime, openModal, showFieldErrors, toast, withLoading,
} from "../ui.js";

const state = { search: "", status: "", admin: null };
let tableHost;

startPage(async () => {
  const { admin, content } = await renderShell({ active: "empleados", title: "Empleados" });
  state.admin = admin;

  const search = h("input", { class: "input", type: "search", placeholder: "Buscar por nombre o identificación…",
    "aria-label": "Buscar empleados" });
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.search = search.value.trim(); load(); }, 300);
  });

  const status = h("select", { class: "select", "aria-label": "Filtrar por estado" },
    h("option", { value: "" }, "Todos los estados"),
    h("option", { value: "active" }, "Activos"),
    h("option", { value: "inactive" }, "Inactivos"));
  status.addEventListener("change", () => { state.status = status.value; load(); });

  const create = canEdit(admin)
    ? h("button", { class: "btn btn-primary", type: "button", onclick: () => openEmployeeForm() }, icon("plus"), "Nuevo empleado")
    : null;

  tableHost = h("div", {});
  mount(content,
    h("div", { class: "page-header" },
      h("div", {}, h("h1", {}, "Empleados"),
        h("p", {}, "Crea perfiles, genera códigos de registro y gestiona la vinculación con Telegram.")),
      create,
    ),
    h("div", { class: "card" },
      h("div", { class: "card-header" }, h("div", { class: "toolbar" },
        h("div", { class: "input-icon" }, icon("search"), search), status)),
      tableHost,
    ),
  );
  await load();
});

async function load() {
  mount(tableHost, h("div", { class: "card-body skeletons" }, [1, 2, 3].map(() => h("div", { class: "skeleton" }))));
  const data = await api("/employees" + query({ search: state.search, status: state.status, limit: 200 }));
  if (!data.items.length) {
    const filtered = state.search || state.status;
    return mount(tableHost, h("div", { class: "empty" }, icon("users"),
      h("strong", {}, filtered ? "No hay resultados" : "Todavía no hay empleados"),
      h("span", {}, filtered ? "Prueba con otra búsqueda." : "Crea el primero con «Nuevo empleado».")));
  }
  mount(tableHost, h("div", { class: "table-wrap" }, h("table", { class: "table" },
    h("thead", {}, h("tr", {}, ["Empleado", "Estado", "Telegram", "Registro", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, data.items.map(row)))));
}

function telegramCell(e) {
  if (e.telegram_linked) {
    return h("div", {}, badge(e.telegram_username ? `@${e.telegram_username}` : "Vinculado", "success"),
      h("div", { class: "cell-sub" }, `Desde ${formatDate(e.linked_at)}`));
  }
  if (e.pending_code) {
    return h("div", {},
      e.pending_code.expired ? badge("Código vencido", "danger") : badge("Código pendiente", "warning"),
      h("div", { class: "cell-sub mono" }, e.pending_code.hint),
      h("div", { class: "cell-sub" }, `${e.pending_code.expired ? "Venció" : "Vence"} ${formatDateTime(e.pending_code.expires_at)}`));
  }
  return badge("Sin vincular");
}

function action(iconName, label, onClick, danger = false) {
  return h("button", { class: `icon-btn ${danger ? "danger" : ""}`, type: "button", title: label, "aria-label": label,
    onclick: onClick }, icon(iconName));
}

function row(e) {
  const name = `${e.first_name} ${e.last_name}`;
  const active = e.status === "active";
  const actions = [];
  if (canEdit(state.admin)) {
    actions.push(action("edit", "Editar", () => openEmployeeForm(e)));
    if (active && !e.telegram_linked) actions.push(action("key", "Generar código de registro", () => generateCode(e)));
    if (e.pending_code) actions.push(action("ban", "Revocar código", () => revokeCode(e)));
    if (e.telegram_linked) actions.push(action("unlink", "Desvincular Telegram", () => unlink(e)));
    actions.push(action("power", active ? "Desactivar" : "Activar", () => toggleStatus(e)));
    actions.push(action("trash", "Eliminar", () => remove(e), true));
  }
  return h("tr", {},
    h("td", { "data-label": "Empleado" }, h("div", { class: "cell-main" }, name), h("div", { class: "cell-sub" }, e.internal_id)),
    h("td", { "data-label": "Estado" }, active ? badge("Activo", "success") : badge("Inactivo")),
    h("td", { "data-label": "Telegram" }, telegramCell(e)),
    h("td", { "data-label": "Registro" }, formatDate(e.created_at)),
    h("td", {}, h("div", { class: "actions" }, actions)),
  );
}

// ---------- Crear / editar ----------

function openEmployeeForm(employee = null) {
  const editing = Boolean(employee);
  const form = h("form", { novalidate: true },
    field({ name: "first_name", label: "Nombre", value: employee?.first_name || "", placeholder: "Ej. Jhon", autocomplete: "off" }),
    field({ name: "last_name", label: "Apellido", value: employee?.last_name || "", placeholder: "Ej. Pérez", autocomplete: "off" }),
    field({ name: "internal_id", label: "Identificación interna", value: employee?.internal_id || "", placeholder: "Ej. EMP-001", autocomplete: "off" }),
    editing ? field({ name: "status", label: "Estado", value: employee.status, options: [["active", "Activo"], ["inactive", "Inactivo"]] }) : null,
  );
  const error = h("div", { class: "form-error hidden", role: "alert" });
  const save = h("button", { class: "btn btn-primary", type: "submit" }, icon("check"), editing ? "Guardar cambios" : "Crear empleado");
  save.setAttribute("form", "employee-form");
  form.id = "employee-form";

  const modal = openModal({
    title: editing ? "Editar empleado" : "Nuevo empleado",
    content: [form, error],
    actions: [h("button", { class: "btn btn-ghost", type: "button", onclick: () => modal.close() }, "Cancelar"), save],
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    error.classList.add("hidden");
    withLoading(save, async () => {
      try {
        if (editing) await api(`/employees/${employee.id}`, { method: "PATCH", body: values });
        else await api("/employees", { method: "POST", body: values });
        modal.close();
        toast(editing ? "Empleado actualizado" : "Empleado creado");
        await load();
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        const fieldErrors = err.fieldErrors;
        if (err.status === 409) fieldErrors.internal_id = err.message;
        showFieldErrors(form, fieldErrors);
        if (!Object.keys(fieldErrors).length) {
          error.textContent = err.message;
          error.classList.remove("hidden");
        }
      }
    });
  });
}

// ---------- Código de registro ----------

async function generateCode(e) {
  if (e.pending_code && !(await confirmDialog({
    title: "Generar un código nuevo",
    message: `${e.first_name} ya tiene un código pendiente. Si generas uno nuevo, el anterior dejará de funcionar.`,
    confirmText: "Generar nuevo",
  }))) return;

  try {
    const issued = await api(`/employees/${e.id}/registration-code`, { method: "POST" });
    showCode(e, issued);
    await load();
  } catch (err) {
    toast(err.message, "error");
  }
}

function showCode(e, issued) {
  const copy = h("button", { class: "btn btn-sm", type: "button" }, icon("copy"), "Copiar");
  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(issued.code);
      copy.replaceChildren(icon("check"), "Copiado");
    } catch {
      toast("No se pudo copiar; selecciona el código manualmente.", "error");
    }
  });
  const modal = openModal({
    title: "Código de registro",
    content: [
      h("p", { class: "muted" }, `Entrégale este código a ${e.first_name} ${e.last_name}:`),
      h("div", { class: "code-box" }, h("span", { class: "code-value", "data-testid": "registration-code" }, issued.code), copy),
      h("p", { class: "muted" }, `Sirve una sola vez y vence el ${formatDateTime(issued.expires_at)}`),
      h("ol", { class: "muted" },
        h("li", {}, "El empleado abre el bot de Telegram de la empresa y escribe /start."),
        h("li", {}, "Acepta la autorización de tratamiento de datos."),
        h("li", {}, "Escribe este código.")),
      h("div", { class: "notice" }, icon("alert"),
        h("span", {}, "Por seguridad, el código completo solo se muestra ahora. Después solo verás su pista.")),
    ],
    actions: [h("button", { class: "btn btn-primary", type: "button", onclick: () => modal.close() }, "Listo")],
  });
}

async function revokeCode(e) {
  if (!(await confirmDialog({ title: "Revocar código", message: `El código pendiente de ${e.first_name} dejará de funcionar.`,
    confirmText: "Revocar", danger: true }))) return;
  await run(() => api(`/employees/${e.id}/registration-code`, { method: "DELETE" }), "Código revocado");
}

async function unlink(e) {
  if (!(await confirmDialog({ title: "Desvincular Telegram",
    message: `${e.first_name} ya no podrá usar el bot hasta que le generes un código nuevo.`,
    confirmText: "Desvincular", danger: true }))) return;
  await run(() => api(`/employees/${e.id}/unlink-telegram`, { method: "POST" }), "Telegram desvinculado");
}

async function toggleStatus(e) {
  const activate = e.status !== "active";
  if (!activate && !(await confirmDialog({ title: "Desactivar empleado",
    message: `${e.first_name} no podrá registrar gastos y su código pendiente se revocará.`,
    confirmText: "Desactivar", danger: true }))) return;
  await run(() => api(`/employees/${e.id}`, { method: "PATCH", body: { status: activate ? "active" : "inactive" } }),
    activate ? "Empleado activado" : "Empleado desactivado");
}

async function remove(e) {
  if (!(await confirmDialog({ title: "Eliminar empleado",
    message: `¿Eliminar a ${e.first_name} ${e.last_name}? Si tiene gastos, viáticos o documentos, no se podrá eliminar (solo desactivar).`,
    confirmText: "Eliminar", danger: true }))) return;
  await run(() => api(`/employees/${e.id}`, { method: "DELETE" }), "Empleado eliminado");
}

async function run(task, successMessage) {
  try {
    await task();
    toast(successMessage);
    await load();
  } catch (err) {
    toast(err.message, "error");
  }
}
