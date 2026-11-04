import { api, query } from "../api.js";
import { areaChart, meter, rankedBars, segmentedBar } from "../charts.js";
import { h, icon, mount } from "../dom.js";
import { renderShell, startPage } from "../layout.js";
import { countUp } from "../motion.js";
import { badge, formatCOP, formatDate } from "../ui.js";

// Cada estado tiene SIEMPRE el mismo color (paleta validada con el skill dataviz:
// orden fijo, colores parecidos nunca adyacentes).
const STATUS = {
  approved: { label: "Aprobado", tone: "approved", badge: "success" },
  submitted: { label: "Enviado", tone: "submitted", badge: "info" },
  pending_completion: { label: "Pendiente de completar", tone: "pending", badge: "warning" },
  in_review: { label: "En revisión", tone: "review", badge: "violet" },
  rejected: { label: "Rechazado", tone: "rejected", badge: "danger" },
  draft: { label: "Borrador", tone: "", badge: "" },
  closed: { label: "Cerrado", tone: "approved", badge: "" },
};
const SEGMENT_ORDER = ["approved", "submitted", "pending_completion", "in_review", "rejected"];
const COUNTED = new Set(["submitted", "in_review", "approved", "closed"]); // lo que cuenta como gastado

const dayKey = new Intl.DateTimeFormat("en-CA", { timeZone: "America/Bogota" }); // AAAA-MM-DD
const dayLabel = new Intl.DateTimeFormat("es-CO", { day: "numeric", month: "short", timeZone: "UTC" });
const monthName = new Intl.DateTimeFormat("es-CO", { month: "long", timeZone: "America/Bogota" });
const compactCOP = new Intl.NumberFormat("es-CO", { notation: "compact", maximumFractionDigits: 1 });
const compact = (n) => (n === 0 ? "$0" : `$${compactCOP.format(n)}`);

/** Día de la factura (AAAA-MM-DD): la fecha de expedición o, si falta, la de recepción en Bogotá. */
const expenseDay = (e) => e.issue_date || dayKey.format(new Date(e.received_at));

function lastDays(n) {
  const today = dayKey.format(new Date());
  const [y, m, d] = today.split("-").map(Number);
  return Array.from({ length: n }, (_, i) => {
    const date = new Date(Date.UTC(y, m - 1, d - (n - 1 - i)));
    return { key: date.toISOString().slice(0, 10), label: dayLabel.format(date) };
  });
}

function card(title, hint, ...body) {
  return h("section", { class: "card dash-card" },
    h("header", { class: "dash-card-head" }, h("h2", {}, title), hint ? h("p", {}, hint) : null),
    ...body);
}

function metric(label, value, hint, { lit = false } = {}) {
  const valueEl = h("div", { class: "metric-value" }, "0");
  countUp(valueEl, value, undefined, { duration: 900 });
  return h("div", { class: `metric ${lit ? "is-lit" : ""}` },
    h("div", { class: "metric-label" }, label), valueEl, h("div", { class: "metric-hint" }, hint));
}

function emptyNote(text) {
  return h("div", { class: "dash-empty" }, icon("receipt"), h("span", {}, text));
}

startPage(async () => {
  const { admin, content } = await renderShell({ active: "dashboard", title: "Dashboard" });
  mount(content, h("div", { class: "card-body skeletons" }, [1, 2, 3].map(() => h("div", { class: "skeleton" }))));

  // Nota: se leen hasta 200 gastos y se agregan aquí. Con más volumen, un endpoint de
  // estadísticas en el backend hará estos cálculos (paso 7.4).
  const [expenses, employees, openViaticos] = await Promise.all([
    api("/expenses" + query({ limit: 200 })),
    api("/employees" + query({ limit: 200 })),
    api("/viaticos" + query({ status: "open" })),
  ]);
  const items = expenses.items;
  const counted = items.filter((e) => COUNTED.has(e.status) && e.total !== null);

  // --- Gasto del mes y mes anterior ---
  const today = dayKey.format(new Date());
  const thisMonth = today.slice(0, 7);
  const [ty, tm] = thisMonth.split("-").map(Number);
  const prevMonth = new Date(Date.UTC(ty, tm - 2, 1)).toISOString().slice(0, 7);
  const sumMonth = (month) => counted.filter((e) => expenseDay(e).startsWith(month)).reduce((s, e) => s + Number(e.total), 0);
  const monthTotal = sumMonth(thisMonth);
  const prevTotal = sumMonth(prevMonth);
  const delta = prevTotal > 0 ? (monthTotal - prevTotal) / prevTotal : null;

  // --- Serie diaria de 30 días ---
  const days = lastDays(30);
  const byDay = new Map(days.map((d) => [d.key, 0]));
  for (const e of counted) {
    const key = expenseDay(e);
    if (byDay.has(key)) byDay.set(key, byDay.get(key) + Number(e.total));
  }
  const series = days.map((d) => ({ ...d, value: byDay.get(d.key) }));

  // --- Estados, categorías y viáticos ---
  const byStatus = Object.fromEntries(SEGMENT_ORDER.map((st) => [st, 0]));
  const amountByStatus = Object.fromEntries(SEGMENT_ORDER.map((st) => [st, 0]));
  for (const e of items) {
    if (!(e.status in byStatus)) continue;
    byStatus[e.status] += 1;
    amountByStatus[e.status] += Number(e.total || 0);
  }
  const segmentTotal = SEGMENT_ORDER.reduce((sum, key) => sum + byStatus[key], 0);
  const byCategory = new Map();
  for (const e of counted) {
    const name = e.category?.name || "Sin categoría";
    byCategory.set(name, (byCategory.get(name) || 0) + Number(e.total));
  }
  const pending = byStatus.submitted + byStatus.in_review;
  const linked = employees.items.filter((e) => e.telegram_linked).length;

  // --- Cifra principal + tendencia ---
  const heroValue = h("div", { class: "hero-value" }, formatCOP(0));
  countUp(heroValue, monthTotal, (n) => formatCOP(Math.round(n)), { duration: 1300 });
  const deltaEl = delta === null
    ? h("span", { class: "hero-delta" }, "Sin datos del mes anterior para comparar")
    : h("span", { class: `hero-delta ${delta > 0 ? "is-up" : "is-down"}` },
        icon(delta > 0 ? "trend-up" : "trend-down"),
        `${delta > 0 ? "+" : ""}${Math.round(delta * 100)} % frente a ${monthName.format(new Date(Date.UTC(ty, tm - 2, 15)))}`);

  const hasSeries = series.some((p) => p.value > 0);
  const hero = h("section", { class: "card hero" },
    h("div", { class: "hero-head" },
      h("div", {},
        h("p", { class: "hero-label" }, `Gastado en ${monthName.format(new Date())}`),
        heroValue,
        deltaEl),
      h("p", { class: "hero-note" }, "Incluye gastos enviados, en revisión y aprobados.")),
    hasSeries
      ? areaChart({ points: series, format: formatCOP, compact, caption: "Gasto diario de los últimos 30 días" })
      : emptyNote("Cuando tu equipo registre gastos, aquí verás cómo evoluciona el gasto diario."));

  // --- Viáticos abiertos ---
  const viaticoRows = openViaticos
    .map((v) => ({ v, ...meter({ spent: Number(v.balance.spent), delivered: Number(v.balance.delivered) - Number(v.balance.refunded) }) }))
    .sort((a, b) => b.ratio - a.ratio)
    .slice(0, 5)
    .map(({ v, el, level, ratio }) => h("li", { class: "vt-row" },
      h("div", { class: "vt-top" },
        h("div", {}, h("div", { class: "cell-main" }, `${v.employee.first_name} ${v.employee.last_name}`),
          h("div", { class: "cell-sub" }, v.purpose || "Viático")),
        h("div", { class: "vt-left" },
          h("strong", {}, formatCOP(v.balance.remaining)), h("span", {}, "disponible"))),
      el,
      h("div", { class: "vt-foot" },
        level !== "ok" ? h("span", { class: `vt-flag is-${level}` }, icon("alert"),
          level === "danger" ? "Gastó más de lo entregado" : "Casi sin saldo") : h("span", {}),
        h("span", { class: "cell-sub" }, `${Math.round(ratio * 100)} % de ${formatCOP(Number(v.balance.delivered) - Number(v.balance.refunded))}`))));

  // --- Gastos recientes (también es la "vista de tabla" del panel) ---
  const recent = items.slice(0, 6).map((e) => {
    const status = STATUS[e.status] || { label: e.status, badge: "" };
    return h("tr", {},
      h("td", { "data-label": "Empleado" }, h("div", { class: "cell-main" }, `${e.employee.first_name} ${e.employee.last_name}`)),
      h("td", { "data-label": "Descripción" }, e.description || "—"),
      h("td", { "data-label": "Fecha" }, formatDate(e.issue_date || e.received_at)),
      h("td", { "data-label": "Total", class: "num" }, formatCOP(e.total)),
      h("td", { "data-label": "Estado" }, badge(status.label, status.badge)));
  });

  mount(content,
    h("div", { class: "page-header" },
      h("div", {}, h("h1", {}, `Hola, ${admin.full_name.split(" ")[0]}`),
        h("p", {}, pending
          ? `Tienes ${pending} ${pending === 1 ? "gasto" : "gastos"} esperando revisión.`
          : "No hay gastos esperando revisión."))),
    hero,
    // El indicador que pide atención se enciende solo si hay algo que revisar.
    h("div", { class: "card metrics mt" },
      metric("Gastos por revisar", pending, `${byStatus.in_review} en revisión`, { lit: pending > 0 }),
      metric("Aprobados", byStatus.approved, "Listos para informes"),
      metric("Empleados activos", employees.items.filter((e) => e.status === "active").length, `${employees.total} registrados`),
      metric("Con Telegram", linked, `${employees.total - linked} sin vincular`)),
    h("div", { class: "dash-grid" },
      // El subtítulo cuenta lo mismo que la barra (los borradores no son un estado de revisión).
      card("Facturas por estado", `${segmentTotal} ${segmentTotal === 1 ? "factura enviada" : "facturas enviadas"} (sin contar borradores)`,
        segmentTotal
          ? segmentedBar({
              caption: "Facturas por estado",
              parts: SEGMENT_ORDER.map((key) => ({ label: STATUS[key].label, value: byStatus[key],
                tone: STATUS[key].tone, detail: formatCOP(amountByStatus[key]) })),
            })
          : emptyNote("Aún no hay facturas.")),
      card("Gasto por categoría", "Gastos enviados y aprobados",
        byCategory.size
          ? rankedBars({ caption: "Gasto por categoría", format: formatCOP,
              items: [...byCategory].map(([label, value]) => ({ label, value })) })
          : emptyNote("Aún no hay gastos con categoría."))),
    h("div", { class: "dash-grid is-wide-right" },
      card("Viáticos abiertos", openViaticos.length ? "Los que van más gastados, primero" : null,
        viaticoRows.length ? h("ul", { class: "vt-list" }, viaticoRows) : emptyNote("No hay viáticos abiertos.")),
      card("Gastos recientes", null,
        recent.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
              h("thead", {}, h("tr", {},
                h("th", {}, "Empleado"), h("th", {}, "Descripción"), h("th", {}, "Fecha"),
                h("th", { class: "num" }, "Total"), h("th", {}, "Estado"))),
              h("tbody", {}, recent)))
          : emptyNote("Cuando un empleado registre un gasto por Telegram, aparecerá aquí."))),
  );
});
