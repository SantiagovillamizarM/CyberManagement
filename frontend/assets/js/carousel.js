/**
 * Carrusel automático de la portada.
 *
 * El tiempo lo marca la barra de progreso del punto activo: es una animación CSS y,
 * cuando termina (animationend), pasa a la siguiente escena. Así pausar es trivial:
 * al pasar el mouse se congela la animación (animation-play-state) y con ella el reloj.
 * Con movimiento reducido no avanza solo; se cambia con los puntos.
 */

import { h } from "./dom.js";
import { reducedMotion } from "./motion.js";

const LEAVE_MS = 520;

export function carousel(slides, { interval = 6500 } = {}) {
  const auto = !reducedMotion();
  let current = -1;

  const stage = h("div", { class: "carousel-stage" });
  const captions = h("div", { class: "carousel-captions", "aria-live": "polite" });
  const dots = h("div", { class: "carousel-dots", role: "tablist", "aria-label": "Funciones del bot" });

  const items = slides.map((slide, index) => {
    const scene = h("div", { class: "carousel-slide", role: "group", "aria-roledescription": "diapositiva",
      "aria-label": `${index + 1} de ${slides.length}: ${slide.title}`, "aria-hidden": "true" });
    // Sin numeración "01/02…": las funciones no son pasos de un proceso; los puntos ya dicen dónde vas.
    const caption = h("div", { class: "carousel-caption", "aria-hidden": "true" },
      h("strong", {}, slide.title),
      h("p", {}, slide.text));
    const progress = h("span", { class: "carousel-progress" });
    const dot = h("button", { class: "carousel-dot", type: "button", role: "tab", "aria-selected": "false",
      "aria-label": slide.title }, h("span", { class: "carousel-bar" }, progress));
    dot.addEventListener("click", () => show(index));
    progress.addEventListener("animationend", () => show((index + 1) % slides.length));
    stage.append(scene);
    captions.append(caption);
    dots.append(dot);
    return { slide, scene, caption, dot };
  });

  function show(index) {
    if (index === current) return;
    const previous = items[current];
    if (previous) {
      for (const el of [previous.scene, previous.caption]) {
        el.classList.remove("is-active");
        el.classList.add("is-leaving");
        el.setAttribute("aria-hidden", "true");
        setTimeout(() => el.classList.remove("is-leaving"), LEAVE_MS);
      }
      previous.dot.classList.remove("is-active");
      previous.dot.setAttribute("aria-selected", "false");
    }

    const next = items[index];
    // Escena nueva en cada vuelta: su animación vuelve a empezar desde cero.
    next.scene.replaceChildren(next.slide.scene());
    for (const el of [next.scene, next.caption]) {
      el.classList.remove("is-leaving");
      el.classList.add("is-active");
      el.removeAttribute("aria-hidden");
    }
    next.dot.style.setProperty("--interval", `${interval}ms`);
    next.dot.classList.toggle("is-auto", auto);
    next.dot.classList.add("is-active");
    next.dot.setAttribute("aria-selected", "true");
    current = index;
  }

  const root = h("div", { class: "carousel" }, stage, captions, dots);
  // Pausa mientras la persona mira una escena con el mouse encima.
  root.addEventListener("pointerenter", () => root.classList.add("is-paused"));
  root.addEventListener("pointerleave", () => root.classList.remove("is-paused"));
  show(0);
  return root;
}
