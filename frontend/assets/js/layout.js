/** Estructura común del panel: verifica la sesión y dibuja el menú y la barra superior. */

import { api } from "./api.js";
import { h, icon, mount } from "./dom.js";
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

/**
 * Dibuja el panel y devuelve { admin, content }.
 * Si no hay sesión, api() redirige al login antes de mostrar nada.
 */
export async function renderShell({ active, title }) {
  const admin = await api("/auth/me");
  document.title = `${title} · CyberManagement`;

  const app = h("div", { class: "app" });
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
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      location.replace("/login.html");
    }
  });

  const toggle = h("button", { class: "icon-btn menu-toggle", type: "button", "aria-label": "Abrir menú" }, icon("menu"));
  toggle.addEventListener("click", () => app.classList.toggle("menu-open"));

  const topbar = h("header", { class: "topbar" },
    toggle,
    h("div", { class: "topbar-title" }, title),
    h("div", { class: "topbar-spacer" }),
    h("div", { class: "user-chip" },
      h("div", { class: "who" }, h("strong", {}, admin.full_name), h("small", {}, ROLE_LABELS[admin.role] || admin.role)),
      h("div", { class: "avatar", "aria-hidden": "true" }, initials(admin.full_name)),
      logout,
    ),
  );

  const content = h("main", { class: "content", id: "content" });
  mount(app, sidebar, h("div", { class: "main" }, topbar, content));
  mount(document.body, app);
  return { admin, content };
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
