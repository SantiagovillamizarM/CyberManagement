import { api, query } from "../api.js";
import { areaChart, meter, rankedBars, segmentedBar } from "../charts.js";
import { h, icon, mount } from "../dom.js";
import { renderShell, startPage } from "../layout.js";
import { countUp } from "../motion.js";
import { badge, EXPENSE_STATUS, formatCOP, formatDate } from "../ui.js";

const STATUS = EXPENSE_STATUS;
const SEGMENT_ORDER = ["approved", "submitted", "pending_completion", "in_review", "rejected"];

const dayLabel = new Intl.DateTimeFormat("es-CO", { day: "numeric", month: "short", timeZone: "UTC" });
const monthName = new Intl.DateTimeFormat("es-CO", { month: "long", timeZone: "America/Bogota" });
const compactCOP = new Intl.NumberFormat("es-CO", { notation: "compact", maximumFractionDigits: 1 });
const compact = (n) => (n === 0 ? "$0" : `$${compactCOP.format(n)}`);

/** Un día "AAAA-MM-DD" de la serie del servidor → punto del gráfico (sin conversión de zona horaria). */
function dayPoint(day) {
  const [y, m, d] = day.date.split("-").map(Number);
  return { key: day.date, label: dayLabel.format(new Date(Date.UTC(y, m - 1, d))), value: Number(day.total) };
}

function card(title, hint, ...body) {
  return h("section", { class: "card dash-card" },
    h("header", { class: "dash-card-head" }, h("h2", {}, title), hint ? h("p", {}, hint) : null),
    ...body);
}

function metric(label, value, hint, { lit = false, money = false, danger = false } = {}) {
  const valueEl = h("div", { class: "metric-value num" }, money ? formatCOP(0) : "0");
  countUp(valueEl, value, money ? (n) => formatCOP(Math.round(n)) : undefined, { duration: 900 });
  return h("div", { class: `metric ${lit ? "is-lit" : ""}` },
    h("div", { class: "metric-label" }, label), valueEl,
    h("div", { class: `metric-hint ${danger ? "is-danger" : ""}` }, danger ? [icon("alert"), hint] : hint));
}

function emptyNote(text) {
  return h("div", { class: "dash-empty" }, icon("receipt"), h("span", {}, text));
}

startPage(async () => {
  const { admin, content } = await renderShell({ active: "dashboard", title: "Dashboard" });
  mount(content, h("div", { class: "card-body skeletons" }, [1, 2, 3].map(() => h("div", { class: "skeleton" }))));

  // Las cifras las calcula el servidor en la base de datos (paso 7.4): son exactas sin importar
  // cuántos gastos haya. Aquí solo se dibujan. Los viáticos abiertos y los gastos recientes son listas cortas.
  const [stats, openViaticos, recentExpenses] = await Promise.all([
    api("/stats/dashboard"),
    api("/viaticos" + query({ status: "open" })),
    api("/expenses" + query({ limit: 6 })),
  ]);
  const num = (v) => Number(v || 0);
  const byStatus = Object.fromEntries(Object.entries(stats.by_status).map(([k, v]) => [k, v.count]));
  const amountByStatus = Object.fromEntries(Object.entries(stats.by_status).map(([k, v]) => [k, num(v.total)]));
  const segmentTotal = SEGMENT_ORDER.reduce((sum, key) => sum + (byStatus[key] || 0), 0);
  const pending = (byStatus.submitted || 0) + (byStatus.in_review || 0);
  const monthTotal = num(stats.month.total);
  const delta = stats.month.change;
  const [ty, tm] = stats.today.split("-").map(Number);

  // --- Cifra principal + tendencia ---
  const heroValue = h("div", { class: "hero-value" }, formatCOP(0));
  countUp(heroValue, monthTotal, (n) => formatCOP(Math.round(n)), { duration: 1300 });
  const deltaEl = delta === null
    ? h("span", { class: "hero-delta" }, "Sin datos del mes anterior para comparar")
    : h("span", { class: `hero-delta ${delta > 0 ? "is-up" : "is-down"}` },
        icon(delta > 0 ? "trend-up" : "trend-down"),
        `${delta > 0 ? "+" : ""}${Math.round(delta * 100)} % frente a ${monthName.format(new Date(Date.UTC(ty, tm - 2, 15)))}`);

  const series = stats.daily.map(dayPoint);
  const hasSeries = series.some((p) => p.value > 0);
  const hero = h("section", { class: "card hero" },
    h("div", { class: "hero-head" },
      h("div", {},
        h("p", { class: "hero-label" }, `Gastado en ${monthName.format(new Date(Date.UTC(ty, tm - 1, 15)))}`),
        heroValue,
        deltaEl),
      h("p", { class: "hero-note" }, "Incluye gastos enviados, en revisión, aprobados y cerrados.")),
    hasSeries
      ? areaChart({ points: series, format: formatCOP, compact, caption: "Gasto diario de los últimos 30 días" })
      : emptyNote("Cuando tu equipo registre gastos, aquí verás cómo evoluciona el gasto diario."));

  // --- Viáticos abiertos (cada uno lleva a su detalle) ---
  const viaticoRows = openViaticos
    .map((v) => ({ v, ...meter({ spent: num(v.balance.spent), delivered: num(v.balance.delivered) - num(v.balance.refunded) }) }))
    .sort((a, b) => b.ratio - a.ratio)
    .slice(0, 5)
    .map(({ v, el, level, ratio }) => h("li", { class: "vt-row" },
      h("a", { class: "vt-row-link", href: `/viaticos.html?id=${v.id}` },
        h("div", { class: "vt-top" },
          h("div", {}, h("div", { class: "cell-main" }, `${v.employee.first_name} ${v.employee.last_name}`),
            h("div", { class: "cell-sub" }, v.purpose || "Viático")),
          h("div", { class: "vt-left" },
            h("strong", {}, formatCOP(v.balance.remaining)), h("span", {}, "disponible"))),
        el,
        h("div", { class: "vt-foot" },
          level !== "ok" ? h("span", { class: `vt-flag is-${level}` }, icon("alert"),
            level === "danger" ? "Gastó más de lo entregado" : "Casi sin saldo") : h("span", {}),
          h("span", { class: "cell-sub" }, `${Math.round(ratio * 100)} % de ${formatCOP(num(v.balance.delivered) - num(v.balance.refunded))}`)))));

  // --- Gastos recientes (también es la "vista de tabla" del panel) ---
  const recent = recentExpenses.items.map((e) => {
    const status = STATUS[e.status] || { label: e.status, badge: "" };
    return h("tr", {},
      h("td", { "data-label": "Empleado" }, h("div", { class: "cell-main" }, `${e.employee.first_name} ${e.employee.last_name}`)),
      h("td", { "data-label": "Descripción" }, h("a", { class: "row-link", href: `/gastos.html?id=${e.id}` }, e.description || "Ver factura")),
      h("td", { "data-label": "Fecha" }, formatDate(e.issue_date || e.received_at)),
      h("td", { "data-label": "Total", class: "num" }, formatCOP(e.total)),
      h("td", { "data-label": "Estado" }, badge(status.label, status.badge)));
  });

  const v = stats.viaticos;
  const photos = stats.unregistered_photos;
  mount(content,
    h("div", { class: "page-header" },
      h("div", {}, h("h1", {}, `Hola, ${admin.full_name.split(" ")[0]}`),
        h("p", {}, pending
          ? `Tienes ${pending} ${pending === 1 ? "gasto" : "gastos"} esperando revisión.`
          : "No hay gastos esperando revisión."))),
    hero,
    // El indicador que pide atención se enciende solo si hay algo que revisar.
    h("div", { class: "card metrics mt" },
      metric("Gastos por revisar", pending, `${byStatus.in_review || 0} en revisión`, { lit: pending > 0 }),
      metric("Aprobados", byStatus.approved || 0, `${stats.counted_count} gastos registrados en total`),
      metric("Empleados activos", stats.employees.active, `${stats.employees.total} registrados`),
      metric("Con Telegram", stats.employees.telegram_linked, `${stats.employees.total - stats.employees.telegram_linked} sin vincular`)),
    // Requerimiento 4: el dinero entregado no es lo mismo que lo gastado.
    h("div", { class: "card metrics mt dash-viaticos" },
      metric("Viáticos entregados", num(v.delivered), `${v.open} ${v.open === 1 ? "viático abierto" : "viáticos abiertos"}`, { money: true }),
      metric("Gastado de viáticos", num(v.spent), num(v.delivered) ? `${Math.round((num(v.spent) / num(v.delivered)) * 100)} % de lo entregado` : "—", { money: true }),
      metric("Saldo restante", num(v.remaining), v.overspent ? `${v.overspent} gastó más de lo entregado` : "En manos de los empleados",
        { money: true, danger: v.overspent > 0 }),
      metric("Fotos sin registrar", photos, photos ? "El bot se lo recuerda al empleado cada día" : "Ninguna pendiente")),
    h("div", { class: "dash-grid" },
      // El subtítulo cuenta lo mismo que la barra (los borradores no son un estado de revisión).
      card("Facturas por estado", `${segmentTotal} ${segmentTotal === 1 ? "factura enviada" : "facturas enviadas"} (sin contar borradores)`,
        segmentTotal
          ? segmentedBar({
              caption: "Facturas por estado",
              parts: SEGMENT_ORDER.map((key) => ({ label: STATUS[key].label, value: byStatus[key] || 0,
                tone: STATUS[key].tone, detail: formatCOP(amountByStatus[key] || 0) })),
            })
          : emptyNote("Aún no hay facturas.")),
      card("Gasto por categoría", "Gastos enviados, aprobados y cerrados",
        stats.by_category.length
          ? rankedBars({ caption: "Gasto por categoría", format: formatCOP,
              items: stats.by_category.map((c) => ({ label: c.name, value: num(c.total) })) })
          : emptyNote("Aún no hay gastos con categoría."))),
    h("div", { class: "dash-grid" },
      card("Gasto por empleado", stats.by_employee.length ? "Los que más han gastado" : null,
        stats.by_employee.length
          ? rankedBars({ caption: "Gasto por empleado", format: formatCOP,
              items: stats.by_employee.map((e) => ({ label: e.name, value: num(e.total) })) })
          : emptyNote("Aún no hay gastos registrados.")),
      card("Viáticos abiertos", openViaticos.length ? "Los que van más gastados, primero" : null,
        viaticoRows.length ? h("ul", { class: "vt-list" }, viaticoRows) : emptyNote("No hay viáticos abiertos."))),
    h("div", { class: "mt" },
      card("Gastos recientes", h("a", { class: "row-link", href: pending ? "/gastos.html?status=review" : "/gastos.html" },
        pending ? "Revisar los pendientes →" : "Ver todas las facturas →"),
        recent.length
          ? h("div", { class: "table-wrap" }, h("table", { class: "table" },
              h("thead", {}, h("tr", {},
                h("th", {}, "Empleado"), h("th", {}, "Descripción"), h("th", {}, "Fecha"),
                h("th", { class: "num" }, "Total"), h("th", {}, "Estado"))),
              h("tbody", {}, recent)))
          : emptyNote("Cuando un empleado registre un gasto por Telegram, aparecerá aquí."))),
  );
});
