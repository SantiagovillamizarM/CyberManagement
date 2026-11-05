/**
 * Informes PDF (paso 8.2): elegir filtros, generar, VER en el navegador y descargar si se quiere.
 *
 * Requerimiento 8 ("Vista previa"): el PDF no se descarga solo. Se muestra en un iframe del
 * mismo sitio (el servidor lo permite con X-Frame-Options: SAMEORIGIN solo para este archivo).
 */

import { api, ApiError, query } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { renderShell, startPage } from "../layout.js";
import { EXPENSE_STATUS, formatCOP, formatDate, formatDateTime, toast, withLoading } from "../ui.js";

// Por defecto, lo que cuenta como gastado (igual que el servidor y el dashboard).
const DEFAULT_STATUSES = ["submitted", "in_review", "approved", "closed"];
const STATUS_ORDER = ["submitted", "in_review", "approved", "closed", "rejected", "pending_completion", "draft"];

const todayKey = () => new Intl.DateTimeFormat("en-CA", { timeZone: "America/Bogota" }).format(new Date());
const iso = (y, m, d) => new Date(Date.UTC(y, m - 1, d)).toISOString().slice(0, 10);
const PRESETS = [
  { key: "month", label: "Este mes", range: (y, m, d) => [iso(y, m, 1), iso(y, m, d)] },
  { key: "previous", label: "Mes anterior", range: (y, m) => [iso(y, m - 1, 1), iso(y, m, 0)] },
  { key: "last30", label: "Últimos 30 días", range: (y, m, d) => [iso(y, m, d - 29), iso(y, m, d)] },
  { key: "year", label: "Este año", range: (y, m, d) => [iso(y, 1, 1), iso(y, m, d)] },
];

const state = { employees: [], categories: [] };
let previewHost, historyHost;

startPage(async () => {
  const { content } = await renderShell({ active: "informes", title: "Informes" });
  const [employees, categories] = await Promise.all([api("/employees" + query({ limit: 200 })), api("/categories")]);
  state.employees = employees.items;
  state.categories = categories;

  previewHost = h("section", { class: "card rp-preview", "aria-label": "Vista previa del informe" });
  historyHost = h("div", {});
  mount(content,
    h("div", { class: "page-header" },
      h("div", {}, h("h1", {}, "Informes"),
        h("p", {}, "Genera el informe de gastos en PDF, revísalo aquí mismo y descárgalo cuando quieras."))),
    h("div", { class: "rp-layout" }, form(), previewHost),
    historyHost,
  );
  showEmptyPreview();
  await loadHistory();
  const id = new URLSearchParams(location.search).get("id");
  if (id) {
    const report = (await api("/reports" + query({ limit: 100 }))).find((r) => r.id === id);
    if (report) showReport(report);
    else toast("Ese informe no existe.", "error");
  }
});

// ---------- Formulario ----------

function form() {
  const [y, m, d] = todayKey().split("-").map(Number);
  const [from, to] = PRESETS[0].range(y, m, d);
  const dateFrom = h("input", { class: "input", type: "date", id: "f-date_from", name: "date_from", value: from, max: todayKey() });
  const dateTo = h("input", { class: "input", type: "date", id: "f-date_to", name: "date_to", value: to, max: todayKey() });

  const presets = h("div", { class: "chips", role: "group", "aria-label": "Período rápido" },
    PRESETS.map((preset, i) => {
      const chip = h("button", { type: "button", class: `chip ${i === 0 ? "is-active" : ""}`, "aria-pressed": String(i === 0) },
        preset.label);
      chip.addEventListener("click", () => {
        [dateFrom.value, dateTo.value] = preset.range(y, m, d);
        markPreset(chip);
      });
      return chip;
    }));
  const markPreset = (active) => {
    for (const chip of presets.children) {
      chip.classList.toggle("is-active", chip === active);
      chip.setAttribute("aria-pressed", String(chip === active));
    }
  };
  for (const input of [dateFrom, dateTo]) input.addEventListener("input", () => markPreset(null));

  const select = (name, label, options) => h("div", { class: "field" },
    h("label", { class: "label", for: `f-${name}` }, label),
    h("select", { class: "select", id: `f-${name}`, name },
      h("option", { value: "" }, "Todos"), options.map(([value, text]) => h("option", { value }, text))));

  const statuses = h("fieldset", { class: "rp-statuses" },
    h("legend", { class: "label" }, "Estados de las facturas"),
    h("div", { class: "rp-checks" }, STATUS_ORDER.map((status) => h("label", { class: "rp-check" },
      h("input", { type: "checkbox", name: "statuses", value: status, checked: DEFAULT_STATUSES.includes(status) }),
      h("span", {}, EXPENSE_STATUS[status].label)))),
    h("p", { class: "cell-sub" }, "Por defecto: lo que cuenta como gastado."));

  const photos = h("label", { class: "rp-check rp-toggle" },
    h("input", { type: "checkbox", name: "include_photos", checked: true }),
    h("span", {}, "Incluir las fotos de las facturas como anexos"));

  const error = h("div", { class: "form-error hidden", role: "alert" });
  const submit = h("button", { class: "btn btn-primary btn-block", type: "submit" }, icon("report"), "Generar informe");
  const el = h("form", { class: "card card-body rp-form", novalidate: true, "aria-label": "Filtros del informe" },
    h("h2", { class: "card-title" }, "¿Qué incluir?"),
    h("div", { class: "field" }, h("span", { class: "label" }, "Período"), presets),
    h("div", { class: "rp-dates" },
      h("div", { class: "field" }, h("label", { class: "label", for: "f-date_from" }, "Desde"), dateFrom),
      h("div", { class: "field" }, h("label", { class: "label", for: "f-date_to" }, "Hasta"), dateTo)),
    select("employee_id", "Empleado", state.employees.map((e) => [e.id, `${e.first_name} ${e.last_name}`])),
    select("category_id", "Tipo de gasto", state.categories.map((c) => [c.id, c.name])),
    statuses, photos, error, submit);

  el.addEventListener("submit", (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    const data = new FormData(el);
    const body = {
      date_from: dateFrom.value, date_to: dateTo.value,
      employee_id: data.get("employee_id") || null, category_id: data.get("category_id") || null,
      statuses: data.getAll("statuses"), include_photos: data.get("include_photos") === "on",
    };
    const problem = !body.date_from || !body.date_to ? "Elige la fecha inicial y la final."
      : body.date_to < body.date_from ? "La fecha final no puede ser anterior a la inicial."
        : !body.statuses.length ? "Elige al menos un estado de factura." : null;
    if (problem) {
      error.textContent = problem;
      error.classList.remove("hidden");
      return;
    }
    withLoading(submit, async () => {
      showGenerating(body.include_photos);
      try {
        const report = await api("/reports", { method: "POST", body });
        showReport(report);
        toast("Informe listo. Revísalo y descárgalo si lo necesitas.");
        await loadHistory();
      } catch (err) {
        if (!(err instanceof ApiError)) throw err;
        showEmptyPreview();
        error.textContent = err.message;
        error.classList.remove("hidden");
      }
    });
  });
  return el;
}

// ---------- Vista previa ----------

function showEmptyPreview() {
  mount(previewHost, h("div", { class: "rp-empty" }, icon("report"),
    h("strong", {}, "Aquí verás el informe"),
    h("span", {}, "Elige los filtros y pulsa «Generar informe». No se descarga hasta que tú lo decidas.")));
}

function showGenerating(withPhotos) {
  mount(previewHost, h("div", { class: "rp-empty", "aria-live": "polite" }, h("span", { class: "spinner" }),
    h("strong", {}, "Generando el informe…"),
    h("span", {}, withPhotos ? "Con fotos puede tardar unos segundos más." : "Un momento.")));
}

function periodLabel(f) {
  return `${formatDate(f.date_from)} al ${formatDate(f.date_to)}`;
}

function showReport(report) {
  const url = new URL(location.href);
  url.searchParams.set("id", report.id);
  history.replaceState(null, "", url);

  const s = report.summary || {};
  const kb = report.size_bytes ? `${Math.max(1, Math.round(report.size_bytes / 1024))} KB` : "";
  mount(previewHost,
    h("div", { class: "rp-toolbar" },
      h("div", {}, h("h2", { class: "card-title" }, `Informe del ${periodLabel(report.filters)}`),
        h("p", { class: "cell-sub" }, `Generado ${formatDateTime(report.created_at)} por ${report.generated_by}`)),
      h("div", { class: "rp-actions" },
        h("a", { class: "btn btn-ghost btn-sm", href: report.file_url, target: "_blank", rel: "noopener" },
          icon("external"), "Abrir en otra pestaña"),
        h("a", { class: "btn btn-primary btn-sm", href: `${report.file_url}?download=true`, download: "" },
          icon("download"), "Descargar PDF"))),
    h("ul", { class: "rp-facts" },
      h("li", {}, h("strong", {}, String(s.expenses ?? "—")), " facturas"),
      h("li", {}, h("strong", {}, formatCOP(s.total)), " en total"),
      h("li", {}, h("strong", {}, String(s.photos ?? 0)), " fotos anexas"),
      kb ? h("li", {}, h("strong", {}, kb)) : null),
    s.truncated ? h("div", { class: "notice" }, icon("alert"),
      h("span", {}, "El período tenía demasiadas facturas; el informe incluye las primeras. Usa un rango más corto.")) : null,
    // #view=FitH: el visor de PDF del navegador ajusta la hoja al ancho del recuadro.
    h("iframe", { class: "rp-frame", src: `${report.file_url}#view=FitH`,
      title: `Vista previa del informe del ${periodLabel(report.filters)}` }),
  );
}

// ---------- Informes anteriores ----------

function filtersLabel(f) {
  const parts = [];
  const employee = state.employees.find((e) => e.id === f.employee_id);
  const category = state.categories.find((c) => c.id === f.category_id);
  parts.push(employee ? `${employee.first_name} ${employee.last_name}` : "Todos los empleados");
  if (category) parts.push(category.name);
  const same = f.statuses.length === DEFAULT_STATUSES.length && DEFAULT_STATUSES.every((s) => f.statuses.includes(s));
  if (!same) parts.push(f.statuses.map((s) => EXPENSE_STATUS[s]?.label || s).join(", "));
  if (!f.include_photos) parts.push("sin fotos");
  return parts.join(" · ");
}

async function loadHistory() {
  const list = await api("/reports" + query({ limit: 20 }));
  if (!list.length) return mount(historyHost);
  mount(historyHost, h("div", { class: "card mt" },
    h("div", { class: "card-header" }, h("h2", { class: "card-title" }, "Informes anteriores")),
    h("div", { class: "table-wrap" }, h("table", { class: "table" },
      h("thead", {}, h("tr", {}, ["Generado", "Período", "Filtros", "Facturas", "Total", ""]
        .map((t) => h("th", { class: ["Facturas", "Total"].includes(t) ? "num" : null }, t)))),
      h("tbody", {}, list.map((r) => h("tr", {},
        h("td", { "data-label": "Generado" }, h("div", { class: "cell-main" }, formatDateTime(r.created_at)),
          h("div", { class: "cell-sub" }, r.generated_by)),
        h("td", { "data-label": "Período" }, periodLabel(r.filters)),
        h("td", { "data-label": "Filtros" }, h("span", { class: "cell-sub" }, filtersLabel(r.filters))),
        h("td", { "data-label": "Facturas", class: "num" }, String(r.summary?.expenses ?? "—")),
        h("td", { "data-label": "Total", class: "num" }, formatCOP(r.summary?.total)),
        h("td", {}, h("div", { class: "actions" },
          h("button", { class: "icon-btn", type: "button", title: "Ver", "aria-label": "Ver informe",
            onclick: () => { showReport(r); previewHost.scrollIntoView({ behavior: "smooth", block: "start" }); } },
          icon("external")),
          h("a", { class: "icon-btn", href: `${r.file_url}?download=true`, download: "", title: "Descargar",
            "aria-label": "Descargar informe" }, icon("download")))))))))));
}
