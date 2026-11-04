/**
 * Gráficos del panel en SVG propio (sin librerías: la CSP no admite scripts de terceros).
 *
 * Reglas (skill dataviz): trazos finos, cuadrícula de 1 px sólida y discreta, un solo eje,
 * texto siempre en colores de texto (nunca del color de la serie), etiquetas selectivas,
 * capa de lectura al pasar el mouse o con el teclado, y una tabla equivalente para
 * lectores de pantalla. Los nombres que vienen de la API se insertan con textContent.
 * Toda animación es transform/opacity y respeta prefers-reduced-motion (base.css).
 */

import { h } from "./dom.js";

const NS = "http://www.w3.org/2000/svg";

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) el.setAttribute(key, String(value));
  }
  for (const child of children) if (child) el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return el;
}

// ---------- Tooltip compartido (uno para toda la página) ----------

let tip;
function tooltip() {
  tip ||= document.body.appendChild(h("div", { class: "chart-tip", role: "tooltip", "aria-hidden": "true" }));
  return tip;
}

/** El valor manda (fuerte) y la etiqueta lo acompaña (secundaria). */
function showTip(x, y, value, label, swatch) {
  const el = tooltip();
  el.replaceChildren(
    h("strong", {}, value),
    h("span", {}, swatch ? h("i", { class: "chart-tip-key", dataset: { tone: swatch } }) : null, label),
  );
  el.classList.add("is-visible");
  const { width, height } = el.getBoundingClientRect();
  const left = Math.min(Math.max(8, x - width / 2), innerWidth - width - 8);
  const top = y - height - 14 < 8 ? y + 18 : y - height - 14;
  el.style.setProperty("--tx", `${left}px`);
  el.style.setProperty("--ty", `${top}px`);
}

function hideTip() {
  tip?.classList.remove("is-visible");
}

/** Tabla equivalente, solo para lectores de pantalla (el gráfico nunca es la única vía). */
function srTable(caption, headers, rows) {
  return h("table", { class: "sr-only" },
    h("caption", {}, caption),
    h("thead", {}, h("tr", {}, headers.map((t) => h("th", { scope: "col" }, t)))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c) => h("td", {}, c))))));
}

/** Pasos "limpios" para el eje: 0, 50.000, 100.000… */
function niceMax(value) {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  // Pasos intermedios para que el eje no quede con medio gráfico vacío (520 mil → 600 mil, no 1 M).
  const multiplier = [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10].find((m) => m * magnitude >= value);
  return multiplier * magnitude;
}

// ---------- Área: tendencia de una sola serie ----------

/**
 * points: [{ key, label, value }] en orden cronológico.
 * format: formatea el valor ("$45.000"); compact: versión corta para el eje.
 */
export function areaChart({ points, format, compact, caption, height = 200 }) {
  const root = h("div", { class: "area-chart" });
  const plot = h("div", { class: "area-plot", tabindex: "0", role: "img", "aria-label": caption });
  root.append(plot, srTable(caption, ["Día", "Valor"], points.map((p) => [p.label, format(p.value)])));

  let index = points.length - 1;
  let geometry = null;

  function render(width) {
    const pad = { top: 18, right: 64, bottom: 28, left: 56 };
    const w = Math.max(width, 280);
    const innerW = w - pad.left - pad.right;
    const innerH = height - pad.top - pad.bottom;
    const max = niceMax(Math.max(...points.map((p) => p.value), 0));
    const x = (i) => pad.left + (points.length === 1 ? innerW : (i / (points.length - 1)) * innerW);
    const y = (v) => pad.top + innerH - (v / max) * innerH;
    geometry = { x, y, pad, innerH };

    const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)} ${y(p.value).toFixed(1)}`).join(" ");
    const area = `${line} L${x(points.length - 1).toFixed(1)} ${pad.top + innerH} L${x(0).toFixed(1)} ${pad.top + innerH} Z`;
    const last = points[points.length - 1];

    const grid = [0, 0.5, 1].map((f) => {
      const gy = pad.top + innerH - f * innerH;
      return s("g", {},
        s("line", { class: "ac-grid", x1: pad.left, x2: w - pad.right, y1: gy, y2: gy }),
        s("text", { class: "ac-tick", x: pad.left - 10, y: gy + 4, "text-anchor": "end" }, compact(max * f)));
    });
    const xLabels = [0, Math.floor((points.length - 1) / 2), points.length - 1].map((i, n) =>
      s("text", { class: "ac-tick", x: x(i), y: height - 6, "text-anchor": ["start", "middle", "end"][n] }, points[i].label));

    const svg = s("svg", { class: "ac-svg", width: w, height, viewBox: `0 0 ${w} ${height}`, "aria-hidden": "true" },
      s("defs", {},
        s("linearGradient", { id: "ac-wash", x1: 0, y1: 0, x2: 0, y2: 1 },
          s("stop", { offset: "0", "stop-color": "#2f6bff", "stop-opacity": "0.22" }),
          s("stop", { offset: "1", "stop-color": "#2f6bff", "stop-opacity": "0" }))),
      ...grid,
      ...xLabels,
      s("path", { class: "ac-area", d: area }),
      s("path", { class: "ac-line", d: line, pathLength: 1 }),
      // Etiqueta selectiva: solo el último valor, al final de la línea.
      s("circle", { class: "ac-end", cx: x(points.length - 1), cy: y(last.value), r: 4 }),
      s("text", { class: "ac-end-label", x: x(points.length - 1) + 10, y: y(last.value) + 4 }, compact(last.value)),
      s("line", { class: "ac-cross", x1: 0, x2: 0, y1: pad.top, y2: pad.top + innerH }),
      s("circle", { class: "ac-cross-dot", r: 4, cx: 0, cy: 0 }),
    );
    plot.replaceChildren(svg);
  }

  /** Lleva la cruz de lectura y el tooltip al día `i` (desde el mouse o el teclado). */
  function focusAt(i) {
    index = Math.max(0, Math.min(points.length - 1, i));
    const svg = plot.querySelector("svg");
    const cx = geometry.x(index);
    const cy = geometry.y(points[index].value);
    svg.classList.add("is-reading");
    svg.querySelector(".ac-cross").setAttribute("transform", `translate(${cx} 0)`);
    const dot = svg.querySelector(".ac-cross-dot");
    dot.setAttribute("cx", cx);
    dot.setAttribute("cy", cy);
    const box = svg.getBoundingClientRect();
    showTip(box.left + cx, box.top + cy, format(points[index].value), points[index].label);
  }

  // El cursor busca el día más cercano: no hay que apuntarle a la línea de 2 px.
  plot.addEventListener("pointermove", (event) => {
    if (!geometry) return;
    const box = plot.getBoundingClientRect();
    const span = box.width - geometry.pad.left - geometry.pad.right;
    const relative = (event.clientX - box.left - geometry.pad.left) / span;
    focusAt(Math.round(relative * (points.length - 1)));
  });
  plot.addEventListener("pointerleave", () => { plot.querySelector("svg")?.classList.remove("is-reading"); hideTip(); });
  // Teclado: flechas recorren los días (lo mismo que el mouse).
  plot.addEventListener("keydown", (event) => {
    if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
      event.preventDefault();
      focusAt(index + (event.key === "ArrowRight" ? 1 : -1));
    }
  });
  plot.addEventListener("focus", () => focusAt(index));
  plot.addEventListener("blur", () => { plot.querySelector("svg")?.classList.remove("is-reading"); hideTip(); });

  // Se redibuja al ancho real del contenedor (texto nítido, sin estirarse).
  const observer = new ResizeObserver(([entry]) => render(Math.round(entry.contentRect.width)));
  observer.observe(plot);
  return root;
}

// ---------- Barra segmentada: un todo dividido en partes ----------

/**
 * parts: [{ label, value, tone, detail? }] en un orden fijo (el color sigue al estado, no al tamaño).
 * Si cada parte trae `detail` (ej. su monto), se usa la variante DELGADA: la barra va sin texto
 * y debajo una lista con las cifras (la lista reemplaza a la leyenda).
 */
export function segmentedBar({ parts, caption, unit = "" }) {
  const withList = parts.some((p) => p.detail);
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  const visible = parts.filter((p) => p.value > 0); // una parte en cero no dibuja nada
  const bar = h("div", { class: `seg-bar ${withList ? "is-thin" : ""}`, role: "img", "aria-label": caption });

  visible.forEach((part, order) => {
    const share = total ? part.value / total : 0;
    const percent = `${Math.round(share * 100)} %`;
    const block = h("div", {
      class: "seg-block", tabindex: "0", dataset: { tone: part.tone },
      "aria-label": `${part.label}: ${part.value}${unit} (${percent})`,
    },
    // Solo se escribe el porcentaje si cabe con holgura (y nunca en la delgada: las cifras van en la lista).
    !withList && share >= 0.11 ? h("span", { class: "seg-label" }, percent) : null);
    block.style.setProperty("--grow", String(part.value));
    block.style.setProperty("--order", String(order));
    const show = (event) => {
      const box = block.getBoundingClientRect();
      showTip(event?.clientX ?? box.left + box.width / 2, box.top, `${part.value}${unit}`, `${part.label}, ${percent}`, part.tone);
    };
    block.addEventListener("pointermove", show);
    block.addEventListener("focus", () => show());
    block.addEventListener("pointerleave", hideTip);
    block.addEventListener("blur", hideTip);
    bar.append(block);
  });

  const legend = withList
    ? h("ul", { class: "seg-list" }, visible.map((part) => h("li", {},
        h("i", { class: "seg-key", dataset: { tone: part.tone } }),
        h("span", { class: "seg-name" }, part.label),
        h("span", { class: "seg-count" }, `${part.value}${unit}`),
        h("strong", { class: "seg-detail" }, part.detail || ""))))
    : h("ul", { class: "seg-legend" },
        visible.map((part) => h("li", {}, h("i", { class: "seg-key", dataset: { tone: part.tone } }), part.label)));

  return h("div", { class: "seg" },
    total ? bar : h("div", { class: "seg-empty" }),
    legend,
    srTable(caption, ["Estado", "Cantidad", "Porcentaje"],
      visible.map((p) => [p.label, `${p.value}${unit}`, `${Math.round((p.value / (total || 1)) * 100)} %`])));
}

// ---------- Barras horizontales ordenadas (un solo tono) ----------

/** items: [{ label, value }]; se ordenan de mayor a menor. */
export function rankedBars({ items, format, caption }) {
  const sorted = [...items].sort((a, b) => b.value - a.value);
  const max = Math.max(...sorted.map((i) => i.value), 1);
  const list = h("ol", { class: "rank", "aria-label": caption });

  sorted.forEach((item, order) => {
    const fill = h("span", { class: "rank-fill" });
    fill.style.setProperty("--w", String(item.value / max));
    fill.style.setProperty("--order", String(order));
    const row = h("li", { class: "rank-row", tabindex: "0", "aria-label": `${item.label}: ${format(item.value)}` },
      h("span", { class: "rank-label" }, item.label),
      h("span", { class: "rank-track" }, fill),
      h("span", { class: "rank-value" }, format(item.value)));
    list.append(row);
  });
  return h("div", {}, list, srTable(caption, ["Categoría", "Total"], sorted.map((i) => [i.label, format(i.value)])));
}

// ---------- Medidor: gastado contra entregado ----------

/** La barra cambia de "acento" a "advertencia" (≥ 85 %) y a "peligro" (> 100 %). */
export function meter({ spent, delivered }) {
  const ratio = delivered > 0 ? spent / delivered : 0;
  const level = ratio > 1 ? "danger" : ratio >= 0.85 ? "warning" : "ok";
  const fill = h("span", { class: "meter-fill", dataset: { level } });
  fill.style.setProperty("--w", String(Math.min(ratio, 1)));
  return {
    level,
    ratio,
    el: h("span", { class: "meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": "100",
      "aria-valuenow": String(Math.round(ratio * 100)), "aria-label": `${Math.round(ratio * 100)} % gastado` }, fill),
  };
}
