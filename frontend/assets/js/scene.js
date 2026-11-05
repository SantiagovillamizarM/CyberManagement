/**
 * Escenas de la portada: un carrusel que cuenta, escena por escena, lo que hace el bot.
 *
 * 1. receiptScene: una factura dibujada con luz que un haz azul escanea. A su paso, cada
 *    renglón borroso se convierte en un dato nítido y el total queda "Verificado".
 * 2. vaultScene: los datos leídos salen de la factura y se guardan en la base de datos.
 * 3. walletScene: el viático de un empleado, entregado vs. gastado.
 * 4. chatScene: la conversación con el bot de Telegram que registra el gasto.
 *
 * Todas miden 300 × 420 para que el carrusel no salte de tamaño. Cada escena se vuelve
 * a construir al mostrarse, así su animación arranca desde cero. Todo el movimiento está
 * en layout.css (.rc-*, .vs-*, .ws-*, .cs-*) y respeta prefers-reduced-motion.
 */

const NS = "http://www.w3.org/2000/svg";

function s(tag, attrs = {}, ...children) {
  const el = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, String(value));
  for (const child of children) el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return el;
}

function today() {
  return new Intl.DateTimeFormat("es-CO", {
    day: "2-digit", month: "2-digit", year: "numeric", timeZone: "America/Bogota",
  }).format(new Date());
}

/** Un renglón: borrones (lo que "ve" la foto) y el dato ya leído (lo que queda verificado). */
function row(index, y, { left, right, leftClass = "", rightClass = "", leftWidth = 90, rightWidth = 56, height = 10 }) {
  const placeholders = [s("rect", { class: "rc-ph", x: 24, y: y - height + 1, width: leftWidth, height, rx: 3 })];
  const values = [s("text", { class: `rc-text ${leftClass}`, x: 24, y }, left)];
  if (right) {
    placeholders.push(s("rect", { class: "rc-ph", x: 276 - rightWidth, y: y - height + 1, width: rightWidth, height, rx: 3 }));
    values.push(s("text", { class: `rc-text ${rightClass}`, x: 276, y, "text-anchor": "end" }, right));
  }
  return s("g", { class: `rc-row rc-r${index}` }, ...placeholders, s("g", { class: "rc-val" }, ...values));
}

function rule(y) {
  return s("line", { class: "rc-rule", x1: 24, x2: 276, y1: y, y2: y });
}

export function receiptScene() {
  // Borde inferior en zigzag: el corte del papel de una factura POS.
  let teeth = "";
  for (let x = 300; x > 0; x -= 30) teeth += ` L${x - 15} 412 L${x - 30} 400`;
  const paper = `M16 0 H284 Q300 0 300 16 V400${teeth} V16 Q0 0 16 0 Z`;

  return s("svg", {
    class: "scene receipt",
    viewBox: "0 0 300 420",
    role: "img",
    "aria-label": "Ilustración: una factura se escanea y sus datos quedan verificados",
  },
    s("defs", {},
      s("linearGradient", { id: "rc-paper", x1: 0, y1: 0, x2: 0, y2: 1 },
        s("stop", { offset: "0", "stop-color": "#16233f", "stop-opacity": "0.92" }),
        s("stop", { offset: "1", "stop-color": "#0a1122", "stop-opacity": "0.9" })),
      s("linearGradient", { id: "rc-trail", x1: 0, y1: 0, x2: 0, y2: 1 },
        s("stop", { offset: "0", "stop-color": "#4d8dff", "stop-opacity": "0" }),
        s("stop", { offset: "1", "stop-color": "#4d8dff", "stop-opacity": "0.38" })),
      s("filter", { id: "rc-blur", x: "-50%", y: "-200%", width: "200%", height: "500%" },
        s("feGaussianBlur", { stdDeviation: 7 }))),

    s("path", { class: "rc-paper", d: paper }),

    row(1, 46, { left: "Restaurante El Cliente", leftClass: "rc-store", leftWidth: 176, height: 13 }),
    row(2, 68, { left: "NIT 900.123.456-8", leftClass: "rc-muted", leftWidth: 120 }),
    rule(86),
    row(3, 112, { left: "Fecha", right: today(), leftClass: "rc-muted", leftWidth: 42, rightWidth: 74 }),
    row(4, 140, { left: "Almuerzo ×2", right: "$40.000", leftWidth: 96 }),
    row(5, 164, { left: "Jugo natural ×2", right: "$10.000", leftWidth: 118 }),
    rule(184),
    row(6, 208, { left: "Subtotal", right: "$50.000", leftClass: "rc-muted", leftWidth: 64 }),
    row(7, 230, { left: "IVA 19 %", right: "$9.500", leftClass: "rc-muted", leftWidth: 60, rightWidth: 48 }),
    rule(250),

    // Total: su resplandor aparece al final, cuando el dato ya está verificado.
    s("g", { class: "rc-total-glow" },
      s("text", { class: "rc-text rc-total rc-total-blurred", x: 276, y: 292, "text-anchor": "end",
        filter: "url(#rc-blur)" }, "$59.500")),
    row(8, 292, { left: "TOTAL", right: "$59.500", leftClass: "rc-total-label", rightClass: "rc-total",
      leftWidth: 54, rightWidth: 104, height: 16 }),

    s("g", { class: "rc-stamp" },
      s("rect", { class: "rc-stamp-box", x: 24, y: 330, width: 118, height: 28, rx: 14 }),
      s("path", { d: "M38 344 l5 5 9-10", fill: "none", stroke: "#2bd9a0", "stroke-width": 2,
        "stroke-linecap": "round", "stroke-linejoin": "round" }),
      s("text", { class: "rc-stamp-text", x: 60, y: 348 }, "Verificado")),

    // El haz de luz que escanea (se mueve con CSS).
    s("g", { class: "rc-beam" },
      s("rect", { class: "rc-beam-trail", x: -12, y: -64, width: 324, height: 64 }),
      s("rect", { x: -16, y: -3, width: 332, height: 8, fill: "#4d8dff", filter: "url(#rc-blur)" }),
      s("rect", { class: "rc-beam-line", x: -16, y: 0, width: 332, height: 2, rx: 1 })),
  );
}

const VIEWBOX = "0 0 300 420";

function t(x, y, text, cls, attrs = {}) {
  return s("text", { class: cls, x, y, ...attrs }, text);
}

/** Los datos de la factura viajan, uno a uno, hasta la base de datos. */
export function vaultScene() {
  // Cada dato sale del centro de la factura y vuelve al centro para caer en el cilindro.
  // El desplazamiento horizontal de cada ficha está en layout.css (.vs-c1…3), no en línea.
  const chips = [
    { x: 18, label: "NIT 900.123" },
    { x: 108, label: today() },
    { x: 198, label: "$59.500" },
  ];
  const arc = (y) => `M64 ${y} A86 20 0 0 0 236 ${y}`;

  return s("svg", { class: "scene", viewBox: VIEWBOX, role: "img",
    "aria-label": "Ilustración: los datos de la factura se guardan en la base de datos" },
    s("defs", {},
      s("linearGradient", { id: "vs-body", x1: 0, y1: 0, x2: 0, y2: 1 },
        s("stop", { offset: "0", "stop-color": "#16233f", "stop-opacity": "0.95" }),
        s("stop", { offset: "1", "stop-color": "#0a1122", "stop-opacity": "0.95" }))),

    s("g", { class: "vs-card" },
      s("rect", { class: "vs-paper", x: 100, y: 18, width: 100, height: 118, rx: 10 }),
      s("rect", { class: "vs-line", x: 114, y: 34, width: 62, height: 7, rx: 3 }),
      s("rect", { class: "vs-line", x: 114, y: 50, width: 44, height: 5, rx: 2.5 }),
      s("rect", { class: "vs-line", x: 114, y: 68, width: 72, height: 5, rx: 2.5 }),
      s("rect", { class: "vs-line", x: 114, y: 80, width: 58, height: 5, rx: 2.5 }),
      s("rect", { class: "vs-line is-lit", x: 140, y: 108, width: 46, height: 9, rx: 3 })),

    ...chips.map((chip, index) =>
      s("g", { class: `vs-chip vs-c${index + 1}` },
        s("rect", { x: chip.x, y: 162, width: 84, height: 26, rx: 13 }),
        t(chip.x + 42, 179, chip.label, "vs-chip-text", { "text-anchor": "middle" }))),

    s("g", { class: "vs-db" },
      s("path", { class: "vs-cyl", d: "M64 268 V352 A86 20 0 0 0 236 352 V268 Z" }),
      s("path", { class: "vs-ring", d: arc(296) }),
      s("path", { class: "vs-ring", d: arc(324) }),
      s("path", { class: "vs-ring vs-lit vs-l1", d: arc(296) }),
      s("path", { class: "vs-ring vs-lit vs-l2", d: arc(324) }),
      s("path", { class: "vs-ring vs-lit vs-l3", d: arc(352) }),
      s("ellipse", { class: "vs-top", cx: 150, cy: 268, rx: 86, ry: 20 }),
      s("ellipse", { class: "vs-pulse", cx: 150, cy: 268, rx: 86, ry: 20 })),

    // Solo se afirma lo que el sistema hace: guarda todo, con historial. (La BD no está
    // cifrada en reposo; no decir "cifrado" aquí.)
    badge(),
  );
}

/*
 * Pastilla "Guardado con historial", centrada en x = 150.
 * Medidas para Archivo 700 a 12 px (el texto mide ~130): margen 18 + ícono 12 + espacio 8 + texto + margen 18.
 * Antes medía 160 y el texto se salía por la derecha. Por si cambian el texto o la fuente, al cargar la
 * fuente se mide el texto real y la pastilla se ajusta (fitBadge).
 */
const BADGE = { cx: 150, y: 382, height: 28, pad: 18, icon: 12, gap: 8 };

function badge() {
  const textWidth = 130;
  const width = BADGE.pad * 2 + BADGE.icon + BADGE.gap + textWidth;
  const x = BADGE.cx - width / 2;
  const rect = s("rect", { x, y: BADGE.y, width, height: BADGE.height, rx: BADGE.height / 2 });
  const check = s("path", { d: checkPath(x + BADGE.pad), fill: "none", stroke: "#2bd9a0", "stroke-width": 2,
    "stroke-linecap": "round", "stroke-linejoin": "round" });
  const label = t(x + BADGE.pad + BADGE.icon + BADGE.gap, 400, "Guardado con historial", "vs-badge-text");
  const group = s("g", { class: "vs-badge" }, rect, check, label);
  document.fonts?.ready.then(() => requestAnimationFrame(() => fitBadge(rect, check, label)));
  return group;
}

const checkPath = (left) => `M${left} 396 l4 4 8-9`;

function fitBadge(rect, check, label) {
  if (!label.isConnected) return;
  let measured;
  try {
    measured = label.getComputedTextLength();
  } catch {
    return; // sin medidas (por ejemplo, el SVG aún no se ve): quedan las medidas por defecto
  }
  if (!measured) return;
  const width = BADGE.pad * 2 + BADGE.icon + BADGE.gap + measured;
  const x = BADGE.cx - width / 2;
  rect.setAttribute("x", x.toFixed(1));
  rect.setAttribute("width", width.toFixed(1));
  check.setAttribute("d", checkPath(x + BADGE.pad));
  label.setAttribute("x", (x + BADGE.pad + BADGE.icon + BADGE.gap).toFixed(1));
}

/** El viático: cuánto se entregó, cuánto se ha gastado y cuánto queda. */
export function walletScene() {
  const bar = (y, cls, width) => [
    s("rect", { class: "ws-track", x: 34, y, width: 232, height: 6, rx: 3 }),
    s("rect", { class: `ws-fill ${cls}`, x: 34, y, width, height: 6, rx: 3 }),
  ];

  return s("svg", { class: "scene", viewBox: VIEWBOX, role: "img",
    "aria-label": "Ilustración: saldo de un viático, entregado contra gastado" },
    s("defs", {},
      s("linearGradient", { id: "ws-grad", x1: 0, y1: 0, x2: 1, y2: 1 },
        s("stop", { offset: "0", "stop-color": "#2f6bff" }),
        s("stop", { offset: "1", "stop-color": "#5ce1ff" }))),

    s("rect", { class: "ws-panel", x: 14, y: 14, width: 272, height: 392, rx: 18 }),
    t(34, 48, "Viático a Medellín", "ws-kicker"),
    t(34, 70, "Juan Pérez", "ws-name"),

    s("g", { class: "ws-gauge" },
      s("circle", { class: "ws-ring-track", cx: 150, cy: 166, r: 70 }),
      s("circle", { class: "ws-arc", cx: 150, cy: 166, r: 70, pathLength: 100, transform: "rotate(-90 150 166)" }),
      t(150, 172, "64 %", "ws-pct", { "text-anchor": "middle" }),
      t(150, 194, "gastado", "ws-pct-sub", { "text-anchor": "middle" })),

    s("g", { class: "ws-row ws-r1" },
      t(34, 276, "Entregado", "ws-label"), t(266, 276, "$500.000", "ws-value", { "text-anchor": "end" }),
      ...bar(286, "is-full", 232)),
    s("g", { class: "ws-row ws-r2" },
      t(34, 322, "Gastado", "ws-label"), t(266, 322, "$320.000", "ws-value", { "text-anchor": "end" }),
      ...bar(332, "is-spent", 148)),

    s("g", { class: "ws-left" },
      s("rect", { x: 34, y: 352, width: 232, height: 36, rx: 10 }),
      t(50, 375, "Disponible", "ws-left-label"),
      t(250, 376, "$180.000", "ws-left-value", { "text-anchor": "end" })),
  );
}

/** La conversación con el bot: foto de la factura → datos leídos → confirmado. */
export function chatScene() {
  return s("svg", { class: "scene", viewBox: VIEWBOX, role: "img",
    "aria-label": "Ilustración: un empleado registra un gasto conversando con el bot de Telegram" },
    s("rect", { class: "cs-panel", x: 14, y: 14, width: 272, height: 392, rx: 22 }),
    s("g", { class: "cs-head" },
      s("circle", { cx: 46, cy: 40, r: 15, fill: "#2f6bff" }),
      s("path", { d: "m53 33-5 15-3-6-6-3 14-6Zm0 0-8 9", fill: "none", stroke: "#fff", "stroke-width": 1.6,
        "stroke-linecap": "round", "stroke-linejoin": "round" }),
      t(70, 37, "CyberManagement Bot", "cs-title"),
      t(70, 53, "en línea", "cs-online"),
      s("line", { class: "cs-divider", x1: 14, x2: 286, y1: 68, y2: 68 })),

    // 1. El empleado manda la foto de la factura.
    s("g", { class: "cs-bubble cs-me cs-b1" },
      s("rect", { x: 150, y: 84, width: 120, height: 90, rx: 14 }),
      s("rect", { class: "cs-photo", x: 178, y: 94, width: 64, height: 70, rx: 4 }),
      s("rect", { class: "cs-photo-line", x: 186, y: 104, width: 40, height: 5, rx: 2 }),
      s("rect", { class: "cs-photo-line", x: 186, y: 116, width: 48, height: 4, rx: 2 }),
      s("rect", { class: "cs-photo-line", x: 186, y: 126, width: 30, height: 4, rx: 2 }),
      s("rect", { class: "cs-photo-line", x: 206, y: 148, width: 28, height: 6, rx: 2 })),

    // 2. El bot "escribe"…
    s("g", { class: "cs-typing" },
      s("rect", { x: 30, y: 190, width: 58, height: 28, rx: 14 }),
      s("circle", { class: "cs-dot", cx: 47, cy: 204, r: 3 }),
      s("circle", { class: "cs-dot", cx: 59, cy: 204, r: 3 }),
      s("circle", { class: "cs-dot", cx: 71, cy: 204, r: 3 })),

    // 3. …y responde con lo que leyó.
    s("g", { class: "cs-bubble cs-bot cs-b2" },
      s("rect", { x: 30, y: 190, width: 204, height: 74, rx: 14 }),
      t(44, 211, "Leí tu factura:", "cs-text cs-muted"),
      t(44, 231, "Restaurante El Cliente", "cs-text cs-strong"),
      t(44, 251, "Total $59.500", "cs-text cs-total")),

    // 4. El empleado confirma.
    s("g", { class: "cs-bubble cs-me cs-b3" },
      s("rect", { x: 168, y: 278, width: 102, height: 30, rx: 15 }),
      s("path", { d: "M182 293 l4 4 7-8", fill: "none", stroke: "#fff", "stroke-width": 1.8,
        "stroke-linecap": "round", "stroke-linejoin": "round" }),
      t(200, 297, "Confirmar", "cs-text cs-on-me")),

    // 5. Queda registrado.
    s("g", { class: "cs-bubble cs-bot cs-b4" },
      s("rect", { x: 30, y: 322, width: 190, height: 30, rx: 15 }),
      s("circle", { cx: 48, cy: 337, r: 8, fill: "rgba(43, 217, 160, 0.16)" }),
      s("path", { d: "M44 337 l3 3 5-6", fill: "none", stroke: "#2bd9a0", "stroke-width": 1.8,
        "stroke-linecap": "round", "stroke-linejoin": "round" }),
      t(62, 341, "Gasto registrado", "cs-text cs-strong")),

    s("g", { class: "cs-input" },
      s("rect", { x: 30, y: 370, width: 240, height: 26, rx: 13 }),
      t(46, 387, "Escribe un mensaje…", "cs-placeholder"),
      s("circle", { cx: 256, cy: 383, r: 9, fill: "#2f6bff" })),
  );
}
