/**
 * Facturas y gastos (paso 7.2): filtrar, revisar con la foto al lado, comparar con lo que
 * leyó la IA, ver el historial, corregir, aprobar y rechazar.
 *
 * Al aprobar o rechazar, Python deja el aviso para el empleado en la bandeja de salida y
 * n8n (CM-06) se lo entrega por Telegram.
 */

import { api, ApiError, query } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { canEdit, renderShell, startPage } from "../layout.js";
import {
  badge, confirmDialog, EXPENSE_STATUS, field, formatCOP, formatDate, formatDateTime, openModal, parseMoney,
  showFieldErrors, statusBadge, toast, withLoading,
} from "../ui.js";

const PAGE = 50;
const QUICK = [
  { key: "", label: "Todos" },
  { key: "review", label: "Por revisar", statuses: ["submitted", "in_review"] },
  { key: "approved", label: "Aprobados", statuses: ["approved"] },
  { key: "rejected", label: "Rechazados", statuses: ["rejected"] },
  { key: "pending", label: "Por completar", statuses: ["pending_completion", "draft"] },
  { key: "closed", label: "Cerrados", statuses: ["closed"] },
];
const SOURCE = {
  ai: { label: "Leído por IA", tone: "info" },
  mixed: { label: "IA + corrección", tone: "violet" },
  manual: { label: "Manual", tone: "" },
};
const LOCATION_SOURCE = {
  telegram: "Compartida por el empleado en Telegram",
  exif: "GPS de la foto",
  manual: "Ingresada manualmente",
};
const PAYMENT = { viatico: "Viático", own_money: "Dinero propio (reembolsable)", company_card: "Tarjeta de la empresa" };
const AI_OUTCOME = {
  extracted: { label: "Leída", tone: "success" },
  unreadable: { label: "Ilegible", tone: "warning" },
  not_invoice: { label: "No es factura", tone: "danger" },
  error: { label: "Falló la lectura", tone: "danger" },
};
const PROCESSING = {
  received: "Recibida", analyzing: "Leyendo…", extracted: "Leída por IA", needs_manual_input: "Ilegible: falta digitarla",
  not_invoice: "No es factura", failed: "Falló la lectura",
};

const state = { admin: null, quick: "", search: "", employee: "", category: "", from: "", to: "", viatico: "", offset: 0 };
let listHost, moreHost, photosHost, categories = [];

startPage(async () => {
  const { admin, content } = await renderShell({ active: "gastos", title: "Facturas" });
  state.admin = admin;
  const params = new URLSearchParams(location.search);
  if (QUICK.some((q) => q.key === params.get("status"))) state.quick = params.get("status");
  state.viatico = params.get("viatico") || "";  // desde Viáticos: solo las facturas de ese viático

  const [employees, cats] = await Promise.all([api("/employees" + query({ limit: 200 })), api("/categories")]);
  categories = cats;

  listHost = h("div", {});
  const viaticoNote = state.viatico ? h("div", { class: "notice notice-info" }, icon("wallet"),
    h("span", {}, "Mostrando solo las facturas de un viático. ",
      h("a", { class: "row-link", href: "/gastos.html" }, "Ver todas →"))) : null;
  moreHost = h("div", { class: "list-more" });
  photosHost = h("div", {});
  mount(content,
    h("div", { class: "page-header" },
      h("div", {}, h("h1", {}, "Facturas y gastos"),
        h("p", {}, "Revisa cada factura junto a su foto, compara lo que leyó la IA y apruébala o recházala."))),
    viaticoNote,
    h("div", { class: "card" },
      h("div", { class: "card-header" }, filters(employees.items)),
      listHost, moreHost),
    photosHost,
  );
  await Promise.all([load(), loadLoosePhotos()]);
  if (params.get("id")) openDetail(params.get("id"));
});

// ---------- Filtros ----------

function filters(employees) {
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Filtrar por estado" },
    QUICK.map((q) => {
      const chip = h("button", { type: "button", class: `chip ${state.quick === q.key ? "is-active" : ""}`,
        "aria-pressed": String(state.quick === q.key) }, q.label);
      chip.addEventListener("click", () => {
        state.quick = q.key;
        for (const other of chips.children) {
          other.classList.toggle("is-active", other === chip);
          other.setAttribute("aria-pressed", String(other === chip));
        }
        reload();
      });
      return chip;
    }));

  const search = h("input", { class: "input", type: "search", placeholder: "Buscar por descripción, NIT o factura…",
    "aria-label": "Buscar facturas" });
  let timer;
  search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.search = search.value.trim(); reload(); }, 300);
  });

  const select = (label, key, options) => {
    const el = h("select", { class: "select", "aria-label": label },
      h("option", { value: "" }, label), options.map(([value, text]) => h("option", { value }, text)));
    el.addEventListener("change", () => { state[key] = el.value; reload(); });
    return el;
  };
  const dateInput = (label, key) => {
    const el = h("input", { class: "input", type: "date", "aria-label": label, title: label });
    el.addEventListener("change", () => { state[key] = el.value; reload(); });
    return el;
  };

  return h("div", { class: "filters" },
    chips,
    h("div", { class: "toolbar" },
      h("div", { class: "input-icon grow" }, icon("search"), search),
      select("Todos los empleados", "employee", employees.map((e) => [e.id, `${e.first_name} ${e.last_name}`])),
      select("Todos los tipos", "category", categories.map((c) => [c.id, c.name])),
      h("div", { class: "date-range" }, dateInput("Fecha de factura desde", "from"), h("span", {}, "a"),
        dateInput("Fecha de factura hasta", "to"))));
}

function listParams() {
  const quick = QUICK.find((q) => q.key === state.quick);
  const params = new URLSearchParams(query({
    search: state.search, employee_id: state.employee, category_id: state.category,
    issue_date_from: state.from, issue_date_to: state.to, viatico_id: state.viatico, limit: PAGE, offset: state.offset,
  }).slice(1));
  for (const status of quick?.statuses || []) params.append("status", status);
  return `?${params}`;
}

function reload() {
  state.offset = 0;
  const url = new URL(location.href);
  if (state.quick) url.searchParams.set("status", state.quick); else url.searchParams.delete("status");
  history.replaceState(null, "", url);
  return load();
}

// ---------- Lista ----------

async function load({ append = false } = {}) {
  if (!append) mount(listHost, h("div", { class: "card-body skeletons" }, [1, 2, 3, 4].map(() => h("div", { class: "skeleton" }))));
  const data = await api("/expenses" + listParams());
  mount(moreHost);

  if (!append && !data.items.length) {
    const filtered = state.quick || state.search || state.employee || state.category || state.from || state.to;
    return mount(listHost, h("div", { class: "empty" }, icon("receipt"),
      h("strong", {}, filtered ? "No hay facturas con estos filtros" : "Todavía no hay facturas"),
      h("span", {}, filtered ? "Prueba con otros filtros." : "Aparecerán aquí cuando los empleados las registren por Telegram.")));
  }

  const rows = data.items.map(row);
  if (append) listHost.querySelector("tbody").append(...rows);
  else {
    mount(listHost, h("div", { class: "table-wrap" }, h("table", { class: "table table-click" },
      h("thead", {}, h("tr", {}, ["Fecha", "Empleado", "Establecimiento", "Descripción", "Total", "Estado"]
        .map((t) => h("th", { class: t === "Total" ? "num" : null }, t)))),
      h("tbody", {}, rows))));
  }

  const shown = state.offset + data.items.length;
  if (shown < data.total) {
    mount(moreHost, h("span", { class: "cell-sub" }, `${shown} de ${data.total}`),
      h("button", { class: "btn btn-sm", type: "button", onclick: (event) => withLoading(event.currentTarget, async () => {
        state.offset = shown;
        await load({ append: true });
      }) }, "Cargar más"));
  } else if (data.total > PAGE) {
    mount(moreHost, h("span", { class: "cell-sub" }, `${data.total} facturas`));
  }
}

function row(e) {
  const source = SOURCE[e.data_source];
  const tr = h("tr", { tabindex: "0", "aria-label": `Ver factura: ${e.description || "sin descripción"}` },
    h("td", { "data-label": "Fecha" }, h("div", { class: "cell-main" }, formatDate(e.issue_date)),
      h("div", { class: "cell-sub" }, `Recibida ${formatDate(e.received_at)}`)),
    h("td", { "data-label": "Empleado" }, `${e.employee.first_name} ${e.employee.last_name}`),
    h("td", { "data-label": "Establecimiento" }, e.supplier
      ? [h("div", { class: "cell-main" }, e.supplier.name), h("div", { class: "cell-sub mono" }, nit(e.supplier))]
      : h("span", { class: "muted" }, "Sin NIT")),
    h("td", { "data-label": "Descripción" }, h("div", {}, e.description || "—"),
      e.data_source !== "manual" ? h("div", { class: "cell-sub ai-hint" }, icon("sparkle"), source.label) : null),
    h("td", { "data-label": "Total", class: "num" }, formatCOP(e.total)),
    h("td", { "data-label": "Estado" }, statusBadge(e.status)),
  );
  tr.addEventListener("click", () => openDetail(e.id));
  tr.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); openDetail(e.id); }
  });
  return tr;
}

const nit = (supplier) => `NIT ${supplier.nit}${supplier.dv ? `-${supplier.dv}` : ""}`;

// ---------- Fotos que nunca quedaron en un gasto ----------

async function loadLoosePhotos() {
  const data = await api("/documents" + query({ unattached: true, kind: "invoice_photo", limit: 24 }));
  const loose = data.items.filter((d) => d.processing_status !== "not_invoice");
  if (!loose.length) return mount(photosHost);
  mount(photosHost, h("div", { class: "card loose-card" },
    h("div", { class: "card-header" }, h("div", {},
      h("h2", { class: "card-title" }, "Fotos sin registrar"),
      h("p", { class: "muted" }, "El empleado envió la foto pero no terminó el registro. El bot se lo recuerda cada día."))),
    h("div", { class: "loose-grid" }, loose.map((d) => h("a", {
      class: "loose-item", href: fileUrl(d), target: "_blank", rel: "noopener",
      title: "Abrir la foto en una pestaña nueva" },
    d.mime_type.startsWith("image/")
      ? h("img", { src: fileUrl(d), alt: "Foto de factura sin registrar", loading: "lazy" })
      : h("div", { class: "file-tile" }, icon("file"), "PDF"),
    h("div", { class: "loose-meta" }, h("strong", {}, formatDateTime(d.received_at)),
      h("span", {}, PROCESSING[d.processing_status] || d.processing_status)))))));
}

const fileUrl = (doc) => `/api/v1/documents/${doc.id}/file`;

// ---------- Detalle ----------

async function openDetail(id) {
  const url = new URL(location.href);
  url.searchParams.set("id", id);
  history.replaceState(null, "", url);

  const body = h("div", { class: "exp-detail" }, h("div", { class: "skeletons" }, [1, 2, 3].map(() => h("div", { class: "skeleton" }))));
  const footer = [];
  const modal = openModal({
    title: "Factura", size: "wide", content: [body], actions: footer,
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
      toast("Esa factura no existe o fue eliminada.", "error");
      return;
    }
    throw err;
  }
}

async function renderDetail(id, body, modal) {
  const [expense, docs, history] = await Promise.all([
    api(`/expenses/${id}`),
    api("/documents" + query({ expense_id: id })),
    api(`/expenses/${id}/history`),
  ]);
  const photo = docs.items.find((d) => d.kind === "invoice_photo");
  const extractions = photo ? await api(`/documents/${photo.id}/extractions`) : [];
  const latest = extractions.at(-1) || null;

  modal.modal.querySelector(".modal-header h2").textContent = expense.description || "Factura";
  mount(body,
    h("div", { class: "exp-photo" }, photoPanel(docs.items)),
    h("div", { class: "exp-info" },
      h("div", { class: "exp-head" },
        h("div", {}, h("div", { class: "exp-total num" }, formatCOP(expense.total)),
          h("div", { class: "cell-sub" }, `${expense.employee.first_name} ${expense.employee.last_name}`)),
        h("div", { class: "exp-badges" }, statusBadge(expense.status),
          badge(SOURCE[expense.data_source].label, SOURCE[expense.data_source].tone))),
      expense.rejection_reason && expense.status === "rejected"
        ? h("div", { class: "notice notice-danger" }, icon("alert"), h("span", {}, h("strong", {}, "Motivo del rechazo: "), expense.rejection_reason))
        : null,
      expense.warnings.length
        ? h("ul", { class: "exp-warnings" }, expense.warnings.map((w) => h("li", {}, icon("alert"), w)))
        : null,
      dataList(expense),
      items(expense),
      aiPanel(expense, photo, latest, () => refresh()),
      historyPanel(history),
    ),
  );
  const buttons = actions(expense, modal, () => refresh());
  if (buttons.length) {
    mount(modal.footer, buttons);
    if (!modal.footer.isConnected) modal.modal.append(modal.footer);  // la ventana se abrió sin botones
  } else {
    modal.footer.remove();
  }

  async function refresh() {
    await renderDetail(id, body, modal);
    await load();
  }
}

function photoPanel(docs) {
  const photo = docs.find((d) => d.kind === "invoice_photo");
  const rest = docs.filter((d) => d !== photo);
  const main = photo
    ? photo.mime_type.startsWith("image/")
      ? h("a", { class: "photo-frame", href: fileUrl(photo), target: "_blank", rel: "noopener", title: "Abrir en tamaño completo" },
        h("img", { src: fileUrl(photo), alt: "Foto de la factura" }), h("span", { class: "photo-open" }, icon("external"), "Ver completa"))
      : h("a", { class: "photo-frame file-tile", href: fileUrl(photo), target: "_blank", rel: "noopener" },
        icon("file"), "Abrir el PDF de la factura")
    : h("div", { class: "photo-frame photo-empty" }, icon("image"), h("strong", {}, "Sin foto"),
      h("span", {}, "El empleado registró este gasto sin foto de la factura."));
  return [
    main,
    photo?.gps ? h("div", { class: "cell-sub" }, icon("pin"), "La foto trae ubicación GPS") : null,
    rest.length ? h("div", { class: "doc-list" }, rest.map((d) => h("a", { href: fileUrl(d), target: "_blank", rel: "noopener", class: "btn btn-sm" },
      icon("file"), d.kind === "rut" ? "RUT del proveedor" : "Otro documento"))) : null,
  ];
}

function dataList(e) {
  const location = e.location_lat !== null
    ? h("a", { href: `https://www.openstreetmap.org/?mlat=${Number(e.location_lat)}&mlon=${Number(e.location_lon)}#map=17/${Number(e.location_lat)}/${Number(e.location_lon)}`,
      target: "_blank", rel: "noopener noreferrer" }, icon("pin"), LOCATION_SOURCE[e.location_source] || "Ver en el mapa")
    : "No disponible";
  const rows = [
    ["Establecimiento", e.supplier ? `${e.supplier.name} · ${nit(e.supplier)}` : "Sin NIT"],
    ["N.º de factura", e.invoice_number || "—"],
    ["Fecha de la factura", `${formatDate(e.issue_date)}${e.issue_time ? ` · ${e.issue_time.slice(0, 5)}` : ""}`],
    ["Recibida", formatDateTime(e.received_at)],
    ["Tipo de gasto", e.category?.name || "—"],
    ["Pago", PAYMENT[e.payment_source] || "—"],
    ["Subtotal · Impuestos", `${formatCOP(e.subtotal)} · ${formatCOP(e.tax_total)}`],
    ["Ubicación", location],
    e.cufe ? ["CUFE", h("span", { class: "mono break" }, e.cufe)] : null,
  ].filter(Boolean);
  return h("dl", { class: "kv" }, rows.map(([k, v]) => [h("dt", {}, k), h("dd", {}, v)]));
}

function items(e) {
  if (!e.items.length) return null;
  return h("div", { class: "exp-section" },
    h("h3", {}, "Productos"),
    h("div", { class: "table-wrap" }, h("table", { class: "table table-compact" },
      h("thead", {}, h("tr", {}, h("th", {}, "Cant."), h("th", {}, "Descripción"), h("th", { class: "num" }, "Unitario"), h("th", { class: "num" }, "Total"))),
      h("tbody", {}, e.items.map((i) => h("tr", {},
        h("td", { class: "num" }, String(Number(i.quantity))), h("td", {}, i.description),
        h("td", { class: "num" }, formatCOP(i.unit_price)), h("td", { class: "num" }, formatCOP(i.line_total))))))));
}

// --- Lo que leyó la IA frente a lo registrado ---

const COMPARE = [
  ["total", "Valor total", (v) => formatCOP(v), (e) => e.total],
  ["subtotal", "Subtotal", (v) => formatCOP(v), (e) => e.subtotal],
  ["tax_total", "Impuestos", (v) => formatCOP(v), (e) => e.tax_total],
  ["issue_date", "Fecha", (v) => formatDate(v), (e) => e.issue_date],
  ["supplier_nit", "NIT", (v) => v || "—", (e) => (e.supplier ? `${e.supplier.nit}-${e.supplier.dv}` : null)],
  ["invoice_number", "N.º de factura", (v) => v || "—", (e) => e.invoice_number],
];

function same(a, b) {
  if (a === null || a === undefined || a === "") return b === null || b === undefined || b === "";
  const na = Number(a), nb = Number(b);
  return Number.isFinite(na) && Number.isFinite(nb) && !String(a).includes("-") ? na === nb : String(a) === String(b);
}

function aiPanel(expense, photo, latest, refresh) {
  if (!photo) return null;
  const header = h("div", { class: "exp-section-head" }, h("h3", {}, icon("sparkle"), "Lectura con IA"));
  if (canEdit(state.admin)) {
    const again = h("button", { class: "btn btn-sm btn-ghost", type: "button" }, icon("refresh"), latest ? "Volver a leer" : "Leer con IA");
    again.addEventListener("click", () => withLoading(again, async () => {
      try {
        const result = await api(`/documents/${photo.id}/analyze`, { method: "POST" });
        const outcome = AI_OUTCOME[result.extraction.outcome];
        toast(`Lectura terminada: ${outcome?.label || result.extraction.outcome}`, result.extraction.outcome === "error" ? "error" : "success");
        await refresh();
      } catch (err) {
        toast(err.message, "error");
      }
    }));
    header.append(again);
  }
  if (!latest) {
    return h("div", { class: "exp-section" }, header, h("p", { class: "muted" }, "Esta foto todavía no se ha leído con IA."));
  }
  const outcome = AI_OUTCOME[latest.outcome] || { label: latest.outcome, tone: "" };
  const meta = h("div", { class: "ai-meta" }, badge(outcome.label, outcome.tone),
    latest.confidence !== null ? h("span", {}, `Confianza ${Math.round(Number(latest.confidence) * 100)} %`) : null,
    h("span", {}, formatDateTime(latest.created_at)));
  const parsed = latest.parsed;
  if (!parsed || latest.outcome === "not_invoice" || latest.outcome === "error") {
    return h("div", { class: "exp-section" }, header, meta,
      h("p", { class: "muted" }, latest.outcome === "error" ? "La lectura falló por un problema técnico. Puedes volver a intentarlo."
        : parsed?.not_invoice_reason || "La IA no reconoció una factura en la imagen."));
  }
  const rows = COMPARE.map(([key, label, show, current]) => {
    const read = parsed[key], now = current(expense);
    const changed = !same(read, now);
    return h("tr", { class: changed ? "is-changed" : null },
      h("td", {}, label), h("td", { class: "num" }, show(read)), h("td", { class: "num" }, show(now)),
      h("td", {}, changed ? badge("Corregido", "warning") : h("span", { class: "ai-ok" }, icon("check"))));
  });
  return h("div", { class: "exp-section" }, header, meta,
    h("div", { class: "table-wrap" }, h("table", { class: "table table-compact ai-compare" },
      h("thead", {}, h("tr", {}, h("th", {}, "Dato"), h("th", { class: "num" }, "Leído por IA"), h("th", { class: "num" }, "Registrado"), h("th", {}, ""))),
      h("tbody", {}, rows))),
    parsed.warnings?.length ? h("ul", { class: "exp-warnings" }, parsed.warnings.map((w) => h("li", {}, icon("alert"), w))) : null);
}

// --- Historial ---

const FIELD = {
  description: "Descripción", total: "Valor total", subtotal: "Subtotal", tax_total: "Impuestos",
  issue_date: "Fecha de la factura", issue_time: "Hora", invoice_number: "N.º de factura", cufe: "CUFE",
  category_id: "Tipo de gasto", supplier_nit: "NIT", status: "Estado", viatico_id: "Viático",
  payment_source: "Pago", expense_id: "Gasto", supplier_id: "Proveedor", items: "Productos",
  location_lat: "Latitud", location_lon: "Longitud", location_text: "Ubicación",
};
const ACTION = {
  create: "registró el gasto", update: "corrigió", delete: "eliminó el gasto", attach: "adjuntó la foto",
  attach_rut: "adjuntó el RUT", status_submitted: "lo envió a revisión", status_in_review: "empezó la revisión",
  status_approved: "lo aprobó", status_rejected: "lo rechazó", status_closed: "lo cerró",
};
const MONEY_FIELDS = new Set(["total", "subtotal", "tax_total"]);

function showValue(fieldName, value) {
  if (value === null || value === undefined || value === "") return "vacío";
  if (MONEY_FIELDS.has(fieldName)) return formatCOP(value);
  if (fieldName === "status") return EXPENSE_STATUS[value]?.label || value;
  if (fieldName === "issue_date") return formatDate(value);
  if (fieldName === "category_id") return categories.find((c) => c.id === value)?.name || "otra categoría";
  if (typeof value === "object") return Array.isArray(value) ? `${value.length} producto(s)` : "—";
  return String(value);
}

function historyPanel(entries) {
  // En el historial, la creación del documento es técnica: se muestran solo los hechos del gasto.
  const visible = entries.filter((e) => !(e.entity_type === "document" && e.action === "create"));
  if (!visible.length) return null;
  return h("div", { class: "exp-section" },
    h("div", { class: "exp-section-head" }, h("h3", {}, icon("history"), "Historial")),
    h("ol", { class: "timeline" }, visible.slice().reverse().map((e) => {
      const what = e.action === "update" && e.field
        ? [`corrigió ${FIELD[e.field] || e.field}: `, h("span", { class: "tl-old" }, showValue(e.field, e.old_value)), " → ",
          h("strong", {}, showValue(e.field, e.new_value))]
        : ACTION[e.action] || e.action;
      return h("li", { class: `tl-${e.actor_type}` },
        h("div", { class: "tl-text" }, h("strong", {}, e.actor_name), " ", what),
        e.reason ? h("div", { class: "tl-reason" }, `“${e.reason}”`) : null,
        h("time", { datetime: e.created_at }, formatDateTime(e.created_at)));
    })));
}

// ---------- Acciones ----------

function actions(expense, modal, refresh) {
  if (!canEdit(state.admin)) return [];
  const list = [];
  const post = (path, message, body) => async (event) => withLoading(event.currentTarget, async () => {
    try {
      await api(`/expenses/${expense.id}/${path}`, { method: "POST", body });
      toast(message);
      await refresh();
    } catch (err) {
      toast(err.message, "error");
    }
  });

  if (expense.status !== "closed") {
    list.push(h("button", { class: "btn btn-ghost btn-danger-text", type: "button", onclick: () => removeExpense(expense, modal) },
      icon("trash"), "Eliminar"));
    list.push(h("button", { class: "btn btn-ghost", type: "button", onclick: () => editExpense(expense, refresh) },
      icon("edit"), "Corregir"));
  }
  if (["draft", "pending_completion", "rejected"].includes(expense.status)) {
    list.push(h("button", { class: "btn", type: "button", onclick: post("submit", "Enviado a revisión") }, "Enviar a revisión"));
  }
  if (expense.status === "submitted") {
    list.push(h("button", { class: "btn", type: "button", onclick: post("start-review", "Revisión iniciada") }, "Empezar revisión"));
  }
  if (["submitted", "in_review"].includes(expense.status)) {
    list.push(h("button", { class: "btn btn-danger", type: "button", onclick: () => rejectExpense(expense, refresh) },
      icon("x"), "Rechazar"));
    list.push(h("button", { class: "btn btn-primary", type: "button",
      onclick: post("approve", "Gasto aprobado. Se le avisará al empleado por Telegram.") }, icon("check"), "Aprobar"));
  }
  if (expense.status === "approved") {
    list.push(h("button", { class: "btn btn-primary", type: "button", onclick: post("close", "Gasto cerrado") }, icon("lock"), "Cerrar"));
  }
  return list;
}

function rejectExpense(expense, refresh) {
  const reason = h("textarea", { class: "input textarea", name: "reason", id: "f-reason", rows: "4", maxlength: "1000",
    placeholder: "Ej. La factura no tiene el NIT del establecimiento." });
  const error = h("div", { class: "field-error", id: "e-reason" });
  const send = h("button", { class: "btn btn-danger", type: "button" }, "Rechazar gasto");
  const modal = openModal({
    title: "Rechazar gasto",
    content: [
      h("p", { class: "muted" }, "El empleado recibirá este motivo por Telegram y podrá corregir y reenviar el gasto desde el bot."),
      h("div", { class: "field" }, h("label", { class: "label", for: "f-reason" }, "Motivo"), reason, error),
    ],
    actions: [h("button", { class: "btn btn-ghost", type: "button", onclick: () => modal.close() }, "Cancelar"), send],
  });
  send.addEventListener("click", () => {
    const text = reason.value.trim();
    if (text.length < 5) {
      error.textContent = "Explica el motivo (mínimo 5 caracteres)";
      reason.setAttribute("aria-invalid", "true");
      return reason.focus();
    }
    withLoading(send, async () => {
      try {
        await api(`/expenses/${expense.id}/reject`, { method: "POST", body: { reason: text } });
        modal.close();
        toast("Gasto rechazado. Se le avisará al empleado por Telegram.");
        await refresh();
      } catch (err) {
        error.textContent = err.message;
      }
    });
  });
}

const MONEY_INPUTS = ["total", "subtotal", "tax_total"];

function editExpense(expense, refresh) {
  const money = (v) => (v === null ? "" : formatCOP(v).replace(/[^\d.,]/g, ""));  // "$ 45.000" → "45.000"
  const form = h("form", { novalidate: true, id: "expense-form", class: "form-grid" },
    h("div", { class: "span-2" }, field({ name: "description", label: "Descripción", value: expense.description || "" })),
    field({ name: "total", label: "Valor total", value: money(expense.total), placeholder: "45.000" }),
    field({ name: "issue_date", label: "Fecha de la factura", type: "date", value: expense.issue_date || "" }),
    field({ name: "subtotal", label: "Subtotal", value: money(expense.subtotal) }),
    field({ name: "tax_total", label: "Impuestos", value: money(expense.tax_total) }),
    field({ name: "supplier_nit", label: "NIT del establecimiento", placeholder: "900.123.456-8",
      value: expense.supplier ? `${expense.supplier.nit}-${expense.supplier.dv}` : "" }),
    field({ name: "supplier_name", label: "Nombre del establecimiento", value: expense.supplier?.name || "" }),
    field({ name: "invoice_number", label: "N.º de factura", value: expense.invoice_number || "" }),
    field({ name: "category_id", label: "Tipo de gasto", value: expense.category?.id || "",
      options: [["", "Sin tipo"], ...categories.map((c) => [c.id, c.name])] }),
  );
  for (const name of MONEY_INPUTS) form.querySelector(`[name=${name}]`).setAttribute("inputmode", "decimal");
  const error = h("div", { class: "form-error hidden", role: "alert" });
  const save = h("button", { class: "btn btn-primary", type: "submit", form: "expense-form" }, icon("check"), "Guardar cambios");
  const modal = openModal({
    title: "Corregir gasto",
    size: "md",
    content: [h("p", { class: "muted" }, "Cada cambio queda en el historial con tu nombre."), form, error],
    actions: [h("button", { class: "btn btn-ghost", type: "button", onclick: () => modal.close() }, "Cancelar"), save],
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    const values = Object.fromEntries(new FormData(form));
    const original = {
      description: expense.description || "", issue_date: expense.issue_date || "", invoice_number: expense.invoice_number || "",
      category_id: expense.category?.id || "", supplier_nit: expense.supplier ? `${expense.supplier.nit}-${expense.supplier.dv}` : "",
      supplier_name: expense.supplier?.name || "",
    };
    const changes = {};
    const fieldErrors = {};
    for (const [key, raw] of Object.entries(values)) {
      const value = raw.trim();
      if (MONEY_INPUTS.includes(key)) {
        const parsed = parseMoney(value);
        if (Number.isNaN(parsed)) { fieldErrors[key] = "Escribe un valor como 45.000"; continue; }
        const before = expense[key] === null ? null : String(Number(expense[key]));
        if ((parsed === null ? null : String(Number(parsed))) !== before) changes[key] = parsed;
      } else if (value !== original[key]) {
        changes[key] = value || null;
      }
    }
    // El NIT y el nombre viajan juntos: el servidor busca o crea el proveedor.
    if ("supplier_nit" in changes || "supplier_name" in changes) {
      changes.supplier_nit = values.supplier_nit.trim() || null;
      changes.supplier_name = values.supplier_name.trim() || null;
    }
    showFieldErrors(form, fieldErrors);
    if (Object.keys(fieldErrors).length) return;
    if (!Object.keys(changes).length) return modal.close();

    withLoading(save, async () => {
      try {
        await api(`/expenses/${expense.id}`, { method: "PATCH", body: { version: expense.version, ...changes } });
        modal.close();
        toast("Gasto corregido");
        await refresh();
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        showFieldErrors(form, err.fieldErrors);
        if (!Object.keys(err.fieldErrors).length) {
          error.textContent = err.message;
          error.classList.remove("hidden");
        }
      }
    });
  });
}

async function removeExpense(expense, detail) {
  if (!(await confirmDialog({
    title: "Eliminar gasto",
    message: "El gasto dejará de contar en los totales y los informes. Queda guardado en el historial de auditoría.",
    confirmText: "Eliminar", danger: true,
  }))) return;
  try {
    await api(`/expenses/${expense.id}`, { method: "DELETE" });
    detail.close();
    toast("Gasto eliminado");
    await load();
  } catch (err) {
    toast(err.message, "error");
  }
}
