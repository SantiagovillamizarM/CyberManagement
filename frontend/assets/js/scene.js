/**
 * Escena de la portada: una factura dibujada con luz que un haz azul escanea.
 * A su paso, cada renglón borroso se convierte en un dato nítido y, al final, el
 * total se enciende con el sello "Verificado". Cuenta en 3 segundos lo que hace
 * el producto: de la foto de la factura al dato verificado.
 *
 * Todo el movimiento está en layout.css (.rc-*) y respeta prefers-reduced-motion.
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
    class: "receipt",
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
