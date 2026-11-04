/**
 * Movimiento del panel: transiciones entre páginas y cifras que cuentan.
 *
 * Regla de fluidez: solo se animan transform y opacity (los resuelve la GPU sin
 * repintar), así el movimiento va a la frecuencia de la pantalla (60, 120, 144 Hz).
 * Todo respeta prefers-reduced-motion.
 */

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ¿El navegador hace View Transitions entre páginas? (Edge/Chrome 126+). Si sí, el
// fundido lo hace él (base.css, @view-transition) y aquí solo se navega.
const nativeTransitions = "PageRevealEvent" in window;

/**
 * Sale de la página con una animación y luego navega.
 * mode "content": solo se desvanece el contenido (el menú se queda quieto, como una app).
 * mode "all": se desvanece toda la vista (cerrar sesión, entrar al panel).
 * pause: ms de espera antes de navegar (p. ej. para que se vea "Bienvenido").
 */
export function leaveTo(url, { mode = "content", replace = false, pause = 0 } = {}) {
  const go = () => (replace ? location.replace(url) : location.assign(url));
  if (reducedMotion()) return go();
  if (nativeTransitions) return setTimeout(go, pause);
  // Navegadores sin View Transitions: animación de salida propia antes de navegar.
  document.body.classList.add(mode === "all" ? "is-leaving-all" : "is-leaving");
  setTimeout(go, pause + (mode === "all" ? 380 : 220));
}

// Al volver con el botón "atrás" la página sale de la caché tal como se dejó: se restaura.
window.addEventListener("pageshow", (event) => {
  if (event.persisted) document.body.classList.remove("is-leaving", "is-leaving-all");
});

/** Intercepta los enlaces internos para que el cambio de página sea suave. */
export function smoothLinks(onNavigate) {
  document.addEventListener("click", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target) return;
    const url = new URL(link.href, location.href);
    if (url.origin !== location.origin || url.pathname === location.pathname) return;
    event.preventDefault();
    onNavigate?.(link);
    leaveTo(url.href);
  });
}

const easeOutExpo = (t) => (t === 1 ? 1 : 1 - 2 ** (-10 * t));

/** Cuenta de 0 al valor final en el elemento (requestAnimationFrame: un paso por cuadro). */
export function countUp(el, value, format = (n) => Math.round(n).toLocaleString("es-CO"), { duration = 1100, delay = 0 } = {}) {
  const target = Number(value) || 0;
  if (reducedMotion() || target === 0) {
    el.textContent = format(target);
    return;
  }
  el.textContent = format(0);
  let start;
  const step = (now) => {
    start ??= now + delay;
    const t = Math.min(Math.max((now - start) / duration, 0), 1);
    el.textContent = format(target * easeOutExpo(t));
    if (t < 1) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}
