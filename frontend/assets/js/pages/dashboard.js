import { api, query } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { renderShell, startPage } from "../layout.js";
import { badge, formatCOP, formatDate } from "../ui.js";

const STATUS = {
  draft: ["Borrador", ""],
  pending_completion: ["Pendiente de completar", "warning"],
  submitted: ["Enviado", "info"],
  in_review: ["En revisión", "violet"],
  approved: ["Aprobado", "success"],
  rejected: ["Rechazado", "danger"],
  closed: ["Cerrado", ""],
};

function metric(label, value, hint, { lit = false } = {}) {
  return h("div", { class: `metric ${lit ? "is-lit" : ""}` },
    h("div", { class: "metric-label" }, label),
    h("div", { class: "metric-value" }, value),
    h("div", { class: "metric-hint" }, hint),
  );
}

startPage(async () => {
  const { admin, content } = await renderShell({ active: "dashboard", title: "Dashboard" });

  const total = (path, params) => api(path + query({ ...params, limit: 1 })).then((r) => r.total);
  const [employees, active, submitted, inReview, approved, recent] = await Promise.all([
    api("/employees" + query({ limit: 200 })),
    total("/employees", { status: "active" }),
    total("/expenses", { status: "submitted" }),
    total("/expenses", { status: "in_review" }),
    total("/expenses", { status: "approved" }),
    api("/expenses" + query({ limit: 6 })),
  ]);
  const linked = employees.items.filter((e) => e.telegram_linked).length;
  const pending = submitted + inReview;

  const rows = recent.items.map((e) => {
    const [label, tone] = STATUS[e.status] || [e.status, ""];
    return h("tr", {},
      h("td", { "data-label": "Empleado" }, h("div", { class: "cell-main" }, `${e.employee.first_name} ${e.employee.last_name}`)),
      h("td", { "data-label": "Descripción" }, e.description || "—"),
      h("td", { "data-label": "Fecha" }, formatDate(e.issue_date || e.received_at)),
      h("td", { "data-label": "Total", class: "num" }, formatCOP(e.total)),
      h("td", { "data-label": "Estado" }, badge(label, tone)),
    );
  });

  mount(content,
    h("div", { class: "page-header" },
      h("div", {}, h("h1", {}, `Hola, ${admin.full_name.split(" ")[0]}`),
        h("p", {}, pending
          ? `Tienes ${pending} ${pending === 1 ? "gasto" : "gastos"} esperando revisión.`
          : "No hay gastos esperando revisión.")),
    ),
    // El primer indicador se enciende solo si hay algo que revisar: la luz significa "acción".
    h("div", { class: "card metrics" },
      metric("Gastos por revisar", pending, `${inReview} en revisión`, { lit: pending > 0 }),
      metric("Aprobados", approved, "Listos para informes"),
      metric("Empleados activos", active, `${employees.total} registrados`),
      metric("Con Telegram", linked, `${employees.total - linked} sin vincular`),
    ),
    h("div", { class: "card mt" },
      h("div", { class: "card-header" }, h("h2", {}, "Gastos recientes")),
      rows.length
        ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
            h("thead", {}, h("tr", {},
              h("th", {}, "Empleado"), h("th", {}, "Descripción"), h("th", {}, "Fecha"),
              h("th", { class: "num" }, "Total"), h("th", {}, "Estado"))),
            h("tbody", {}, rows)))
        : h("div", { class: "empty" }, icon("receipt"), h("strong", {}, "Aún no hay gastos"),
            h("span", {}, "Cuando un empleado registre un gasto por Telegram, aparecerá aquí.")),
    ),
  );
});
