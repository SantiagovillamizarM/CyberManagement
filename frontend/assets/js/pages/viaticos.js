/**
 * Viáticos (paso 7.3): asignar dinero, registrar adicionales, reintegros y ajustes, y cerrar
 * con la liquidación.
 *
 * Regla del requerimiento 4: el dinero ENTREGADO no es lo mismo que lo GASTADO. El saldo
 * nunca se escribe a mano: lo calcula el servidor (entregado − reintegrado − gastado).
 * Asignar, recargar, ajustar y cerrar le avisan al empleado por Telegram (bandeja de salida → CM-06).
 */

import { api, ApiError, query } from "../api.js";
import { meter } from "../charts.js";
import { h, icon, mount } from "../dom.js";
import { canEdit, renderShell, startPage } from "../layout.js";
import {
  badge, confirmDialog, field, formatCOP, formatDate, formatDateTime, openModal, parseMoney, showFieldErrors,
  statusBadge, toast, withLoading,
} from "../ui.js";

const FILTERS = [
  { key: "open", label: "Abiertos" },
  { key: "closed", label: "Cerrados" },
  { key: "", label: "Todos" },
];
const MOVEMENT = {
  initial: { label: "Dinero inicial", icon: "wallet" },
  additional: { label: "Dinero adicional", icon: "plus" },
  refund: { label: "Reintegro del empleado", icon: "refresh" },
  adjustment: { label: "Ajuste", icon: "edit" },
};

const state = { admin: null, status: "open", employee: "", employees: [] };
let summaryHost, listHost;

const num = (v) => Number(v || 0);
const available = (b) => num(b.delivered) - num(b.refunded);  // lo que realmente quedó en manos del empleado
const fullName = (e) => `${e.first_name} ${e.last_name}`;

/** La liquidación contada desde el administrador. */
function settlementText(v) {
  const amount = formatCOP(Math.abs(num(v.balance.remaining)));
  const name = v.employee.first_name;
  return {
    employee_must_return: { text: `${name} debe reintegrar ${amount} a la empresa`, tone: "warning" },
    company_must_reimburse: { text: `La empresa le debe reembolsar ${amount} a ${name}`, tone: "danger" },
    balanced: { text: "Queda a paz y salvo", tone: "success" },
  }[v.balance.settlement];
}

startPage(async () => {
  const { admin, content } = await renderShell({ active: "viaticos", title: "Viáticos" });
  state.admin = admin;
  state.employees = (await api("/employees" + query({ limit: 200 }))).items;

  summaryHost = h("div", {});
  listHost = h("div", { class: "card-body" });
  mount(content,
    h("div", { class: "page-header" },
      h("div", {}, h("h1", {}, "Viáticos"),
        h("p", {}, "El dinero entregado a cada empleado frente a lo que ha gastado. El saldo lo calcula el sistema.")),
      canEdit(admin) ? h("button", { class: "btn btn-primary", type: "button", onclick: () => openViaticoForm() },
        icon("plus"), "Asignar viático") : null),
    summaryHost,
    h("div", { class: "card" }, h("div", { class: "card-header" }, filters()), listHost),
  );
  await load();
  const id = new URLSearchParams(location.search).get("id");
  if (id) openDetail(id);
});

function filters() {
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Filtrar por estado" },
    FILTERS.map((f) => {
      const chip = h("button", { type: "button", class: `chip ${state.status === f.key ? "is-active" : ""}`,
        "aria-pressed": String(state.status === f.key) }, f.label);
      chip.addEventListener("click", () => {
        state.status = f.key;
        for (const other of chips.children) {
          other.classList.toggle("is-active", other === chip);
          other.setAttribute("aria-pressed", String(other === chip));
        }
        load();
      });
      return chip;
    }));
  const employee = h("select", { class: "select", "aria-label": "Filtrar por empleado" },
    h("option", { value: "" }, "Todos los empleados"),
    state.employees.map((e) => h("option", { value: e.id }, fullName(e))));
  employee.addEventListener("change", () => { state.employee = employee.value; load(); });
  return h("div", { class: "filters" }, h("div", { class: "toolbar" }, chips, employee));
}

// ---------- Resumen y lista ----------

async function load() {
  mount(listHost, h("div", { class: "skeletons" }, [1, 2].map(() => h("div", { class: "skeleton" }))));
  const [list, open] = await Promise.all([
    api("/viaticos" + query({ status: state.status, employee_id: state.employee })),
    api("/viaticos" + query({ status: "open", employee_id: state.employee })),
  ]);
  renderSummary(open);

  if (!list.length) {
    return mount(listHost, h("div", { class: "empty" }, icon("wallet"),
      h("strong", {}, state.status === "closed" ? "No hay viáticos cerrados" : "No hay viáticos abiertos"),
      h("span", {}, canEdit(state.admin) && state.status !== "closed"
        ? "Asigna uno con «Asignar viático». El empleado también puede declararlo desde el bot."
        : "Prueba con otro filtro.")));
  }
  // Primero los que más preocupan: los más gastados.
  const sorted = list.slice().sort((a, b) => (a.status === b.status
    ? num(b.balance.spent) / (available(b.balance) || 1) - num(a.balance.spent) / (available(a.balance) || 1)
    : a.status === "open" ? -1 : 1));
  mount(listHost, h("ul", { class: "vt-grid" }, sorted.map(card)));
}

function renderSummary(open) {
  const delivered = open.reduce((s, v) => s + available(v.balance), 0);
  const spent = open.reduce((s, v) => s + num(v.balance.spent), 0);
  const remaining = open.reduce((s, v) => s + num(v.balance.remaining), 0);
  const over = open.filter((v) => num(v.balance.remaining) < 0).length;
  // La luz azul es para datos verificados, no para alertas: un saldo negativo se marca en rojo.
  const metric = (label, value, hint, danger = false) => h("div", { class: "metric" },
    h("div", { class: "metric-label" }, label), h("div", { class: "metric-value num" }, value),
    h("div", { class: `metric-hint ${danger ? "is-danger" : ""}` }, danger ? [icon("alert"), hint] : hint));
  mount(summaryHost, h("div", { class: "card metrics vt-summary" },
    metric("Entregado", formatCOP(delivered), `${open.length} ${open.length === 1 ? "viático abierto" : "viáticos abiertos"}`),
    metric("Gastado", formatCOP(spent), delivered ? `${Math.round((spent / delivered) * 100)} % de lo entregado` : "—"),
    metric("Disponible", formatCOP(remaining), over ? `${over} con gasto de más` : "En manos de los empleados", over > 0)));
}

function card(v) {
  const m = meter({ spent: num(v.balance.spent), delivered: available(v.balance) });
  const closed = v.status === "closed";
  const item = h("li", {}, h("button", { type: "button", class: `vt-card ${closed ? "is-closed" : ""}`,
    "aria-label": `Ver viático ${v.purpose || ""} de ${fullName(v.employee)}` },
  h("div", { class: "vt-top" },
    h("div", {}, h("div", { class: "cell-main" }, v.purpose || "Viático"),
      h("div", { class: "cell-sub" }, fullName(v.employee))),
    h("div", { class: "vt-left" }, h("strong", { class: "num" }, formatCOP(v.balance.remaining)),
      h("span", {}, closed ? "saldo final" : "disponible"))),
  m.el,
  h("div", { class: "vt-foot" },
    closed ? badge("Cerrado")
      : m.level !== "ok" ? h("span", { class: `vt-flag is-${m.level}` }, icon("alert"),
        m.level === "danger" ? "Gastó más de lo entregado" : "Casi sin saldo")
        : h("span", { class: "cell-sub" }, `Desde ${formatDate(v.opened_at)}`),
    h("span", { class: "cell-sub" }, `${formatCOP(v.balance.spent)} de ${formatCOP(available(v.balance))}`))));
  item.firstChild.addEventListener("click", () => openDetail(v.id));
  return item;
}

// ---------- Detalle ----------

async function openDetail(id) {
  const url = new URL(location.href);
  url.searchParams.set("id", id);
  history.replaceState(null, "", url);
  const body = h("div", { class: "vt-detail" }, h("div", { class: "skeletons" }, [1, 2].map(() => h("div", { class: "skeleton" }))));
  const modal = openModal({
    title: "Viático", size: "md", content: [body],
    onClose: () => {
      const clean = new URL(location.href);
      clean.searchParams.delete("id");
      history.replaceState(null, "", clean);
    },
  });
  try {
    await renderDetail(id, body, modal);
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      modal.close();
      return toast("Ese viático no existe.", "error");
    }
    throw err;
  }
}

async function renderDetail(id, body, modal) {
  const [v, spent] = await Promise.all([
    api(`/viaticos/${id}`),
    api("/expenses" + query({ viatico_id: id, limit: 200 })),
  ]);
  const closed = v.status === "closed";
  const refresh = async () => { await renderDetail(id, body, modal); await load(); };
  modal.modal.querySelector(".modal-header h2").textContent = v.purpose || "Viático";

  const b = v.balance;
  const settle = settlementText(v);
  const m = meter({ spent: num(b.spent), delivered: available(b) });
  mount(body,
    h("div", { class: "vt-hero" },
      h("div", {}, h("div", { class: "cell-sub" }, closed ? "Saldo final" : "Disponible"),
        h("div", { class: `exp-total num ${num(b.remaining) < 0 ? "is-negative" : ""}` }, formatCOP(b.remaining)),
        h("div", { class: "cell-sub" }, `${fullName(v.employee)} · desde ${formatDate(v.opened_at)}`)),
      closed ? badge(`Cerrado ${formatDate(v.closed_at)}`) : badge("Abierto", "info")),
    m.el,
    h("dl", { class: "vt-breakdown" },
      [["Entregado", b.delivered], ["Reintegrado", b.refunded], ["Gastado", b.spent], ["De eso, aprobado", b.spent_approved]]
        .map(([k, val]) => h("div", {}, h("dt", {}, k), h("dd", { class: "num" }, formatCOP(val))))),
    h("div", { class: `notice notice-${settle.tone}` }, icon(settle.tone === "success" ? "check" : "alert"),
      h("span", {}, closed ? "Liquidación: " : "Si se cerrara hoy: ", h("strong", {}, settle.text))),
    movementsPanel(v),
    expensesPanel(v, spent),
  );

  const actions = [];
  if (canEdit(state.admin) && !closed) {
    const register = h("button", { class: "btn btn-ghost", type: "button", onclick: () => openMovementForm(v, refresh) },
      icon("plus"), "Registrar movimiento");
    // Si no se puede cerrar, el motivo va a la izquierda; si se puede, "Cerrar" es el botón principal (derecha).
    actions.push(...(v.unresolved_expenses > 0 ? [closeButton(v, refresh), register] : [register, closeButton(v, refresh)]));
  }
  if (actions.length) {
    mount(modal.footer, actions);
    if (!modal.footer.isConnected) modal.modal.append(modal.footer);
  } else {
    modal.footer.remove();
  }
}

function movementsPanel(v) {
  return h("div", { class: "exp-section" },
    h("div", { class: "exp-section-head" }, h("h3", {}, icon("history"), "Movimientos del dinero")),
    h("ol", { class: "timeline" }, v.movements.slice().reverse().map((mv) => {
      const info = MOVEMENT[mv.type] || { label: mv.type };
      const amount = num(mv.amount);
      const sign = mv.type === "refund" ? "−" : amount < 0 ? "−" : "+";
      return h("li", { class: mv.source === "admin" ? "tl-admin" : "tl-employee" },
        h("div", { class: "tl-text vt-move" }, h("span", {}, info.label),
          h("strong", { class: `num ${sign === "−" ? "is-negative" : ""}` }, `${sign} ${formatCOP(Math.abs(amount))}`)),
        mv.note ? h("div", { class: "tl-reason" }, `“${mv.note}”`) : null,
        h("time", { datetime: mv.created_at },
          `${formatDateTime(mv.created_at)} · ${mv.created_by_name || `${v.employee.first_name} (desde el bot)`}`));
    })));
}

function expensesPanel(v, spent) {
  const head = h("div", { class: "exp-section-head" }, h("h3", {}, icon("receipt"), `Facturas (${spent.total})`));
  if (!spent.items.length) {
    return h("div", { class: "exp-section" }, head, h("p", { class: "muted" }, "Todavía no hay facturas pagadas con este viático."));
  }
  return h("div", { class: "exp-section" }, head,
    h("div", { class: "table-wrap" }, h("table", { class: "table table-compact" },
      h("thead", {}, h("tr", {}, h("th", {}, "Fecha"), h("th", {}, "Descripción"), h("th", { class: "num" }, "Total"), h("th", {}, "Estado"))),
      h("tbody", {}, spent.items.map((e) => h("tr", {},
        h("td", { class: "nowrap" }, formatDate(e.issue_date)),
        h("td", {}, h("a", { class: "row-link", href: `/gastos.html?id=${e.id}` }, e.description || "Ver factura")),
        h("td", { class: "num" }, formatCOP(e.total)),
        h("td", {}, statusBadge(e.status))))))));
}

function closeButton(v, refresh) {
  if (v.unresolved_expenses > 0) {
    const n = v.unresolved_expenses;
    return h("div", { class: "vt-blocked" }, icon("lock"),
      h("span", {}, `No se puede cerrar: ${n} ${n === 1 ? "factura está" : "facturas están"} sin aprobar ni rechazar. `,
        h("a", { class: "row-link", href: `/gastos.html?viatico=${v.id}` }, "Revisarlas →")));
  }
  return h("button", { class: "btn btn-primary", type: "button", onclick: async (event) => {
    const button = event.currentTarget;
    const settle = settlementText(v);
    if (!(await confirmDialog({
      title: "Cerrar viático",
      message: `Liquidación: ${settle.text}. Entregado ${formatCOP(available(v.balance))}, gastado ${formatCOP(v.balance.spent)}. `
        + "Después no se podrán registrar más movimientos ni facturas en este viático.",
      confirmText: "Cerrar viático",
    }))) return;
    withLoading(button, async () => {
      try {
        await api(`/viaticos/${v.id}/close`, { method: "POST" });
        toast("Viático cerrado. Se le avisará al empleado por Telegram con la liquidación.");
        await refresh();
      } catch (err) {
        toast(err.message, "error");
      }
    });
  } }, icon("lock"), "Cerrar viático");
}

// ---------- Formularios ----------

function moneyError(value, { allowNegative = false } = {}) {
  const negative = allowNegative && value.trim().startsWith("-");
  const parsed = parseMoney(value.trim().replace(/^-/, ""));
  if (parsed === null) return { error: "Escribe el valor" };
  if (Number.isNaN(parsed)) return { error: "Escribe un valor como 150.000" };
  if (Number(parsed) === 0) return { error: "El valor no puede ser cero" };
  return { amount: negative ? `-${parsed}` : parsed };
}

function openViaticoForm() {
  const active = state.employees.filter((e) => e.status === "active");
  if (!active.length) return toast("Primero crea un empleado activo.", "error");
  const form = h("form", { novalidate: true, id: "viatico-form" },
    field({ name: "employee_id", label: "Empleado", options: active.map((e) => [e.id, fullName(e)]) }),
    field({ name: "initial_amount", label: "Dinero entregado", placeholder: "500.000" }),
    field({ name: "purpose", label: "¿Para qué es?", placeholder: "Ej. Viaje a Medellín (opcional)" }));
  form.querySelector("[name=initial_amount]").setAttribute("inputmode", "decimal");
  const error = h("div", { class: "form-error hidden", role: "alert" });
  const save = h("button", { class: "btn btn-primary", type: "submit", form: "viatico-form" }, icon("check"), "Asignar viático");
  const modal = openModal({
    title: "Asignar viático",
    content: [h("p", { class: "muted modal-lead" }, "El empleado recibirá un aviso por Telegram y podrá elegir este viático al registrar sus gastos."),
      form, error],
    actions: [h("button", { class: "btn btn-ghost", type: "button", onclick: () => modal.close() }, "Cancelar"), save],
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    const money = moneyError(values.initial_amount);
    showFieldErrors(form, money.error ? { initial_amount: money.error } : {});
    if (money.error) return;
    withLoading(save, async () => {
      try {
        const created = await api("/viaticos", { method: "POST", body: {
          employee_id: values.employee_id, initial_amount: money.amount, purpose: values.purpose.trim() || null } });
        modal.close();
        toast("Viático asignado. Se le avisará al empleado por Telegram.");
        state.status = "open";
        await load();
        openDetail(created.id);
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        error.textContent = err.message;
        error.classList.remove("hidden");
      }
    });
  });
}

function openMovementForm(v, refresh) {
  const type = field({ name: "type", label: "Tipo de movimiento", value: "additional", options: [
    ["additional", "Dinero adicional entregado"], ["refund", "Reintegro (el empleado devolvió dinero)"],
    ["adjustment", "Ajuste (corrección; puede ser negativo)"]] });
  const amount = field({ name: "amount", label: "Valor", placeholder: "100.000" });
  const note = field({ name: "note", label: "Nota", placeholder: "Opcional" });
  const preview = h("p", { class: "vt-preview muted", "aria-live": "polite" });
  const form = h("form", { novalidate: true, id: "movement-form" }, type, amount, note, preview);
  const amountInput = form.querySelector("[name=amount]");
  const typeSelect = form.querySelector("[name=type]");
  const noteInput = form.querySelector("[name=note]");
  amountInput.setAttribute("inputmode", "decimal");

  function update() {
    const isAdjustment = typeSelect.value === "adjustment";
    noteInput.placeholder = isAdjustment ? "Obligatoria: explica el ajuste" : "Opcional";
    amountInput.placeholder = isAdjustment ? "-20.000 o 20.000" : "100.000";
    const money = moneyError(amountInput.value, { allowNegative: isAdjustment });
    if (money.error) return mount(preview, `Disponible ahora: ${formatCOP(v.balance.remaining)}`);
    // Adicional y ajuste suman a lo entregado; el reintegro lo resta (el empleado devuelve).
    const delta = typeSelect.value === "refund" ? -num(money.amount) : num(money.amount);
    mount(preview, "Disponible después: ", h("strong", {}, formatCOP(num(v.balance.remaining) + delta)));
  }
  typeSelect.addEventListener("change", update);
  amountInput.addEventListener("input", update);
  update();

  const error = h("div", { class: "form-error hidden", role: "alert" });
  const save = h("button", { class: "btn btn-primary", type: "submit", form: "movement-form" }, icon("check"), "Registrar");
  const modal = openModal({
    title: "Registrar movimiento",
    content: [h("p", { class: "muted modal-lead" }, `${fullName(v.employee)} · ${v.purpose || "Viático"}`), form, error],
    actions: [h("button", { class: "btn btn-ghost", type: "button", onclick: () => modal.close() }, "Cancelar"), save],
  });
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const isAdjustment = typeSelect.value === "adjustment";
    const money = moneyError(amountInput.value, { allowNegative: isAdjustment });
    const errors = {};
    if (money.error) errors.amount = money.error;
    if (isAdjustment && !noteInput.value.trim()) errors.note = "Un ajuste necesita una nota que lo explique";
    showFieldErrors(form, errors);
    if (Object.keys(errors).length) return;
    withLoading(save, async () => {
      try {
        await api(`/viaticos/${v.id}/movements`, { method: "POST", body: {
          type: typeSelect.value, amount: money.amount, note: noteInput.value.trim() || null } });
        modal.close();
        toast("Movimiento registrado. Se le avisará al empleado por Telegram.");
        await refresh();
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        error.textContent = err.message;
        error.classList.remove("hidden");
      }
    });
  });
}
