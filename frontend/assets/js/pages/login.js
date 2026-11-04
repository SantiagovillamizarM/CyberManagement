import { api, ApiError } from "../api.js";
import { h, icon, mount } from "../dom.js";
import { receiptScene } from "../scene.js";

/** Solo se permite volver a una página interna (evita redirecciones a sitios externos). */
function nextPage() {
  const next = new URLSearchParams(location.search).get("next") || "/";
  return next.startsWith("/") && !next.startsWith("//") ? next : "/";
}

async function init() {
  // Si ya hay una sesión activa, no tiene sentido mostrar el login.
  try {
    const response = await fetch("/api/v1/auth/me", { credentials: "same-origin" });
    if (response.ok) return location.replace(nextPage());
  } catch {
    /* sin conexión: se muestra el formulario igual */
  }

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

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    error.classList.add("hidden");
    if (!email.value.trim() || !password.value) {
      error.textContent = "Escribe tu correo y tu contraseña.";
      error.classList.remove("hidden");
      return;
    }
    submit.disabled = true;
    submit.replaceChildren(h("span", { class: "spinner" }), "Verificando…");
    try {
      await api("/auth/login", { method: "POST", body: { email: email.value, password: password.value } });
      location.replace(nextPage());
    } catch (err) {
      error.textContent = err instanceof ApiError ? err.message : "No se pudo iniciar sesión.";
      error.classList.remove("hidden");
      password.value = "";
      password.focus();
      submit.disabled = false;
      submit.replaceChildren("Iniciar sesión");
    }
  });

  mount(document.body,
    h("div", { class: "auth" },
      h("section", { class: "auth-scene", "aria-label": "Cómo funciona CyberManagement" },
        receiptScene(),
        h("ul", { class: "auth-facts" },
          h("li", {}, icon("camera"), "Fotos de facturas guardadas de forma segura"),
          h("li", {}, icon("wallet"), "Viáticos con saldo entregado vs. gastado"),
          h("li", {}, icon("shield"), "Historial de cada cambio"),
        ),
      ),
      h("section", { class: "auth-panel" },
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
  email.focus();
}

init();
