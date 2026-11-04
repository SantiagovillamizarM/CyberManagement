/** Estructura común del panel: verifica la sesión y dibuja el menú y la barra superior. */

import { api } from "./api.js";
import { h, icon, mount } from "./dom.js";
import { leaveTo, smoothLinks } from "./motion.js";
import { confirmDialog, initials, toast } from "./ui.js";

const NAV = [
  { section: "General" },
  { href: "/", label: "Dashboard", icon: "dashboard", key: "dashboard" },
  { href: "/empleados.html", label: "Empleados", icon: "users", key: "empleados" },
  { section: "Finanzas" },
  { href: "/gastos.html", label: "Facturas", icon: "receipt", key: "gastos", soon: true },
  { href: "/viaticos.html", label: "Viáticos", icon: "wallet", key: "viaticos", soon: true },
  { href: "/informes.html", label: "Informes", icon: "report", key: "informes", soon: true },
];

const ROLE_LABELS = { superadmin: "Superadministrador", admin: "Administrador", auditor: "Auditor" };

/*
 * Para que cambiar de página se sienta como una app, el menú se dibuja al instante
 * con el nombre del administrador guardado en sessionStorage (solo nombre y rol, nada
 * sensible), mientras /auth/me confirma la sesión. Si la sesión no sirve, api()
 * redirige al login igual que antes.
 */
const ADMIN_KEY = "cm_admin";
const ENTER_KEY = "cm_enter"; // lo deja el login: la primera vista del panel entra completa

function storage(action, key, value) {
  try {
    if (action === "get") return JSON.parse(sessionStorage.getItem(key));
    if (action === "set") sessionStorage.setItem(key, JSON.stringify(value));
    if (action === "remove") sessionStorage.removeItem(key);
  } catch {
    /* almacenamiento bloqueado: el panel funciona igual, sin el atajo */
  }
  return null;
}

export const rememberAdmin = (admin) => {
  storage("set", ADMIN_KEY, { full_name: admin.full_name, role: admin.role });
  storage("set", ENTER_KEY, true);
};

function buildShell(admin, { active, title, entering }) {
  const app = h("div", { class: `app ${entering ? "is-entering" : ""}` });
  const nav = h("nav", { class: "nav", "aria-label": "Menú principal" },
    NAV.map((item) =>
      item.section
        ? h("div", { class: "nav-section" }, item.section)
        : h("a", {
            href: item.href,
            class: [item.key === active ? "active" : "", item.soon ? "disabled" : ""].join(" ").trim(),
            "aria-current": item.key === active ? "page" : null,
            "aria-disabled": item.soon ? "true" : null,
          }, icon(item.icon), item.label, item.soon ? h("span", { class: "soon" }, "Pronto") : null),
    ),
  );

  const sidebar = h("aside", { class: "sidebar" },
    h("div", { class: "brand" },
      h("div", { class: "brand-mark" }, icon("logo")),
      h("div", { class: "brand-name" }, "Cyber", h("span", {}, "Management")),
    ),
    nav,
    h("div", { class: "sidebar-footer" }, icon("shield"),
      h("span", {}, "Sesión protegida. Se cierra sola tras 60 minutos sin actividad.")),
  );

  const logout = h("button", { class: "icon-btn", type: "button", title: "Cerrar sesión", "aria-label": "Cerrar sesión" }, icon("logout"));
  logout.addEventListener("click", async () => {
    if (!(await confirmDialog({ title: "Cerrar sesión", message: "¿Quieres salir del panel?", confirmText: "Cerrar sesión" }))) return;
    storage("remove", ADMIN_KEY);
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      leaveTo("/login.html", { mode: "all", replace: true });
    }
  });

  const toggle = h("button", { class: "icon-btn menu-toggle", type: "button", "aria-label": "Abrir menú" }, icon("menu"));
  toggle.addEventListener("click", () => app.classList.toggle("menu-open"));

  const name = h("strong", {});
  const role = h("small", {});
  const avatar = h("div", { class: "avatar", "aria-hidden": "true" });
  const setAdmin = (data) => {
    name.textContent = data.full_name;
    role.textContent = ROLE_LABELS[data.role] || data.role;
    avatar.textContent = initials(data.full_name);
  };
  setAdmin(admin);

  const topbar = h("header", { class: "topbar" },
    toggle,
    h("div", { class: "topbar-title" }, title),
    h("div", { class: "topbar-spacer" }),
    h("div", { class: "user-chip" }, h("div", { class: "who" }, name, role), avatar, logout),
  );

  const content = h("main", { class: "content", id: "content" });
  mount(app, sidebar, h("div", { class: "main" }, topbar, content));
  mount(document.body, app);
  if (entering) setTimeout(() => app.classList.remove("is-entering"), 1200);

  // Al elegir otra sección, el indicador se mueve de inmediato mientras sale el contenido.
  smoothLinks((link) => {
    if (!nav.contains(link)) return;
    nav.querySelector("a.active")?.classList.remove("active");
    link.classList.add("active");
    app.classList.remove("menu-open");
  });

  return { content, setAdmin };
}

/**
 * Dibuja el panel y devuelve { admin, content }.
 * Si no hay sesión, api() redirige al login antes de mostrar datos.
 */
export async function renderShell({ active, title }) {
  document.title = `${title} · CyberManagement`;
  const entering = Boolean(storage("get", ENTER_KEY));
  storage("remove", ENTER_KEY);

  const cached = storage("get", ADMIN_KEY);
  let shell = cached ? buildShell(cached, { active, title, entering }) : null;

  const admin = await api("/auth/me");
  storage("set", ADMIN_KEY, { full_name: admin.full_name, role: admin.role });
  if (shell) shell.setAdmin(admin);
  else shell = buildShell(admin, { active, title, entering: true });
  return { admin, content: shell.content };
}

export const canEdit = (admin) => admin.role === "superadmin" || admin.role === "admin";

/** Envuelve el arranque de una página: muestra un error amable si algo falla. */
export function startPage(run) {
  run().catch((error) => {
    if (error?.status === 401) return; // ya se redirigió al login
    console.error(error);
    toast(error?.message || "No se pudo cargar la página.", "error");
  });
}
