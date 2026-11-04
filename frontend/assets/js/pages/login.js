import { api, ApiError } from "../api.js";
import { carousel } from "../carousel.js";
import { h, icon, mount } from "../dom.js";
import { rememberAdmin } from "../layout.js";
import { leaveTo } from "../motion.js";
import { chatScene, receiptScene, vaultScene, walletScene } from "../scene.js";

/** Solo se permite volver a una página interna (evita redirecciones a sitios externos). */
function nextPage() {
  const next = new URLSearchParams(location.search).get("next") || "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

const SLIDES = [
  { title: "Escanea facturas", scene: receiptScene,
    text: "Una foto basta: la IA lee proveedor, NIT, fecha y total, y los verifica." },
  { title: "Guarda cada dato", scene: vaultScene,
    text: "Cada foto y cada dato quedan guardados, con el historial de quién cambió qué." },
  { title: "Viáticos al día", scene: walletScene,
    text: "Lo entregado contra lo gastado de cada empleado, siempre cuadrado." },
  { title: "Todo por Telegram", scene: chatScene,
    text: "Tu equipo registra y corrige sus gastos conversando con el bot." },
];

/*
 * Fondo vivo detrás de la escena: luces que flotan, dos órbitas y chispas que suben.
 * Es decorativo y lento a propósito; solo usa transform/opacity (no repinta).
 * La posición y el ritmo de cada chispa están en layout.css (.sp1…sp8): la CSP no
 * permite el atributo style en línea.
 */
function ambient() {
  const sparks = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => h("span", { class: `ambient-spark sp${n}` }));
  return h("div", { class: "ambient", "aria-hidden": "true" },
    h("span", { class: "ambient-blob b1" }),
    h("span", { class: "ambient-blob b2" }),
    h("span", { class: "ambient-blob b3" }),
    h("span", { class: "ambient-orbit o1" }),
    h("span", { class: "ambient-orbit o2" }),
    h("span", { class: "ambient-sweep" }),
    sparks,
  );
}

function render() {
  const email = h("input", { class: "input", id: "email", name: "email", type: "email", required: true,
    autocomplete: "username", placeholder: "admin@empresa.com" });
  const password = h("input", { class: "input", id: "password", name: "password", type: "password", required: true,
    autocomplete: "current-password", placeholder: "Tu contraseña" });
  const error = h("div", { class: "form-error hidden", role: "alert" });
  const submit = h("button", { class: "btn btn-primary btn-block", type: "submit" }, "Iniciar sesión");

  const form = h("form", { class: "auth-form", novalidate: true },
    h("div", { class: "field" }, h("label", { class: "label", for: "email" }, "Correo"),
      h("div", { class: "input-icon" }, icon("mail"), email)),
    h("div", { class: "field" }, h("label", { class: "label", for: "password" }, "Contraseña"),
      h("div", { class: "input-icon" }, icon("lock"), password)),
    error,
    h("div", { class: "form-actions" }, submit),
  );

  const showError = (message) => {
    error.textContent = message;
    error.classList.remove("hidden");
    // Reinicia la sacudida aunque el error ya estuviera visible.
    form.classList.remove("is-shaking");
    void form.offsetWidth;
    form.classList.add("is-shaking");
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    if (!email.value.trim() || !password.value) return showError("Escribe tu correo y tu contraseña.");
    submit.disabled = true;
    submit.replaceChildren(h("span", { class: "spinner" }), "Verificando…");
    try {
      const { admin } = await api("/auth/login", { method: "POST", body: { email: email.value, password: password.value } });
      rememberAdmin(admin);
      submit.classList.add("is-done");
      submit.replaceChildren(icon("check"), "Bienvenido");
      leaveTo(nextPage(), { mode: "all", replace: true, pause: 420 });
    } catch (err) {
      showError(err instanceof ApiError ? err.message : "No se pudo iniciar sesión.");
      password.value = "";
      password.focus();
      submit.disabled = false;
      submit.replaceChildren("Iniciar sesión");
    }
  });

  mount(document.body,
    h("div", { class: "auth" },
      h("section", { class: "auth-scene", "aria-label": "Cómo funciona CyberManagement" },
        ambient(),
        carousel(SLIDES),
      ),
      h("section", { class: "auth-panel stagger" },
        h("div", { class: "brand" },
          h("div", { class: "brand-mark" }, icon("logo")),
          h("div", { class: "brand-name" }, "Cyber", h("span", {}, "Management")),
        ),
        h("h1", {}, "Cada gasto, verificado."),
        h("p", { class: "lead" },
          "Revisa las facturas que tu equipo envía por Telegram, apruébalas y mantén los viáticos al día."),
        form,
        h("p", { class: "auth-foot" }, "Por seguridad, la cuenta se bloquea 15 minutos tras 5 intentos fallidos."),
        h("p", { class: "auth-copy" }, "© CyberManagement"),
      ),
    ),
  );
  email.focus({ preventScroll: true });
}

async function init() {
  // Si ya hay una sesión activa, no tiene sentido mostrar el login.
  try {
    const response = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
    if (response.ok) return location.replace(nextPage());
  } catch {
    /* sin conexión: se muestra el formulario igual */
  }
  render();
}

init();
