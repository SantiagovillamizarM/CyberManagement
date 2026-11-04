/**
 * Construcción segura de la interfaz.
 *
 * REGLA ANTI-XSS: los datos que vienen de la API (nombres, descripciones...) SIEMPRE
 * se insertan como texto (textContent), nunca como HTML. Por eso no se usa innerHTML
 * con datos: h() crea elementos y agrega los textos como nodos de texto.
 */

/**
 * h("button", { class: "btn", onclick: fn }, "Guardar", icon("save"))
 * - Atributos que empiezan por "on" se registran como eventos.
 * - Los hijos de tipo texto se agregan con document.createTextNode (seguro).
 */
export function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "class") {
      el.className = value;
    } else if (key === "dataset") {
      Object.assign(el.dataset, value);
    } else if (value === true) {
      el.setAttribute(key, "");
    } else {
      el.setAttribute(key, String(value));
    }
  }
  append(el, children);
  return el;
}

function append(parent, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    parent.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

/** Reemplaza todo el contenido de un elemento. */
export function mount(target, ...children) {
  target.replaceChildren();
  append(target, children);
  return target;
}

// Íconos (trazos de 24x24, estilo línea). Son constantes del código, no datos del usuario.
const ICONS = {
  logo: "M12 2 3 7v10l9 5 9-5V7l-9-5Zm0 0v10m0 0 9-5m-9 5-9-5m9 5v10",
  dashboard: "M3 3h8v8H3zM13 3h8v5h-8zM13 10h8v11h-8zM3 13h8v8H3z",
  users: "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm13 10v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75",
  receipt: "M4 2v20l3-2 3 2 3-2 3 2 3-2 1 .7V2l-1 .7-3-2-3 2-3-2-3 2-3-2ZM8 7h8M8 11h8M8 15h5",
  wallet: "M20 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0 0 4h15v13H5a2 2 0 0 1-2-2V5m13 9h.01",
  report: "M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6Zm0 0v6h6M8 13h8M8 17h5",
  logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4m7 14 5-5-5-5m5 5H9",
  menu: "M3 6h18M3 12h18M3 18h18",
  search: "m21 21-4.35-4.35M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z",
  plus: "M12 5v14M5 12h14",
  edit: "M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5Z",
  trash: "M3 6h18M8 6V4h8v2m3 0-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6",
  key: "M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.78 7.78 5.5 5.5 0 0 1 7.78-7.78Zm0 0L15.5 7.5m0 0 3 3L22 7l-3-3m-3.5 3.5L19 4",
  ban: "M4.93 4.93l14.14 14.14M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z",
  unlink: "M18.84 12.25l1.72-1.71a5 5 0 0 0-7.07-7.07l-1.72 1.71M5.17 11.75l-1.71 1.71a5 5 0 0 0 7.07 7.07l1.71-1.71M8 2v3M2 8h3M16 22v-3M22 16h-3",
  power: "M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10",
  copy: "M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2ZM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1",
  check: "M20 6 9 17l-5-5",
  x: "M18 6 6 18M6 6l12 12",
  alert: "M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0ZM12 9v4m0 4h.01",
  telegram: "m22 2-7 20-4-9-9-4 20-7Zm0 0L11 13",
  shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z",
  camera: "M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2zM12 17a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z",
  clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Zm0-16v6l4 2",
  mail: "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Zm18 2-10 7L2 6",
  lock: "M19 11H5a2 2 0 0 0-2 2v7a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7a2 2 0 0 0-2-2ZM7 11V7a5 5 0 0 1 10 0v4",
};

export function icon(name) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.8");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", ICONS[name] || ICONS.alert);
  svg.append(path);
  return svg;
}
