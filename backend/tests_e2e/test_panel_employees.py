"""Paso 7.1 en navegador real: login, menú y sección de empleados (CA-2 a CA-8)."""

import re

from playwright.sync_api import Page, expect

from tests_e2e.conftest import BASE_URL, Credentials, login

CODE = re.compile(r"^JHON-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")


def create_employee(page: Page, first="Jhon", last="Prueba", internal_id="E2E-001") -> None:
    page.click("text=Nuevo empleado")
    page.fill("#f-first_name", first)
    page.fill("#f-last_name", last)
    page.fill("#f-internal_id", internal_id)
    with page.expect_response(lambda r: r.url.endswith("/api/v1/employees") and r.request.method == "POST"):
        page.click(".modal button[type=submit]")
    expect(page.locator("tr", has_text=internal_id)).to_be_visible()


# --- CA-2: acceso ---


def test_ca2_protected_pages_redirect_to_login(page: Page) -> None:
    page.goto(f"{BASE_URL}/empleados.html")

    page.wait_for_url(re.compile(r"/login\.html\?next=%2Fempleados\.html$"))
    expect(page.locator("#email")).to_be_visible()


def test_ca2_wrong_password_shows_generic_error(page: Page, credentials: Credentials) -> None:
    page.goto(f"{BASE_URL}/login.html")
    page.fill("#email", credentials.admin_email)
    page.fill("#password", "contraseña-incorrecta")
    page.click("button[type=submit]")

    expect(page.locator(".form-error")).to_have_text("Correo o contraseña incorrectos")
    expect(page.locator("#password")).to_have_value("")


def test_ca2_login_returns_to_requested_page(page: Page, credentials: Credentials) -> None:
    login(page, credentials, next_path="/empleados.html")

    expect(page.locator("h1")).to_have_text("Empleados")


def test_ca2_external_next_is_ignored(page: Page, credentials: Credentials) -> None:
    """Evita que un enlace malicioso use el login para redirigir a otro sitio."""
    page.goto(f"{BASE_URL}/login.html?next=//evil.example.com")
    page.fill("#email", credentials.admin_email)
    page.fill("#password", credentials.admin_initial_password)
    page.click("button[type=submit]")

    page.wait_for_url(f"{BASE_URL}/")


# --- CA-3: estructura y cierre de sesión ---


def test_ca3_shell_and_logout(page: Page, credentials: Credentials) -> None:
    login(page, credentials)

    expect(page.locator(".nav a.active")).to_have_text("Dashboard")
    expect(page.locator(".user-chip strong")).to_have_text("Administrador")
    page.click("button[aria-label='Cerrar sesión']")
    page.click(".modal .btn-primary")
    page.wait_for_url(re.compile(r"/login\.html"))

    page.goto(f"{BASE_URL}/empleados.html")  # la sesión ya no sirve
    page.wait_for_url(re.compile(r"/login\.html"))


# --- CA-4 y CA-6: empleados y validaciones ---


def test_ca4_create_search_and_filter(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/empleados.html")
    create_employee(page, "Jhon", "Prueba", "E2E-001")
    create_employee(page, "Ana", "Gómez", "E2E-002")

    page.fill("input[type=search]", "góm")
    expect(page.locator("tbody tr")).to_have_count(1)
    expect(page.locator("tbody tr")).to_contain_text("Ana Gómez")


def test_ca6_validation_messages_in_spanish(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/empleados.html")
    page.click("text=Nuevo empleado")

    with page.expect_response(lambda r: r.url.endswith("/api/v1/employees")):
        page.click(".modal button[type=submit]")
    expect(page.locator("#e-first_name")).to_have_text("Este campo es obligatorio")

    page.fill("#f-first_name", "Jhon")
    page.fill("#f-last_name", "Prueba")
    page.fill("#f-internal_id", "E2E 001")  # con espacio
    with page.expect_response(lambda r: r.url.endswith("/api/v1/employees")):
        page.click(".modal button[type=submit]")
    expect(page.locator("#e-internal_id")).to_contain_text("sin espacios")
    expect(page.locator("#e-first_name")).to_have_text("")  # el error anterior se limpia


def test_ca6_duplicate_internal_id(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/empleados.html")
    create_employee(page, internal_id="E2E-001")

    page.click("text=Nuevo empleado")
    page.fill("#f-first_name", "Otro")
    page.fill("#f-last_name", "Empleado")
    page.fill("#f-internal_id", "E2E-001")
    page.click(".modal button[type=submit]")

    expect(page.locator("#e-internal_id")).to_have_text("Ya existe un empleado con esa identificación interna")


def test_ca4_edit_and_deactivate(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/empleados.html")
    create_employee(page)
    row = page.locator("tr", has_text="E2E-001")

    row.locator("button[aria-label=Editar]").click()
    page.fill("#f-first_name", "John")
    page.click(".modal button[type=submit]")
    expect(row).to_contain_text("John Prueba")

    row.locator("button[aria-label=Desactivar]").click()
    page.click(".modal .btn-danger")
    expect(row.locator(".badge").first).to_have_text("Inactivo")
    expect(row.locator("button[aria-label='Generar código de registro']")).to_have_count(0)


# --- CA-5: código de registro ---


def test_ca5_code_shown_once_then_only_hint(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/empleados.html")
    create_employee(page)
    row = page.locator("tr", has_text="E2E-001")

    row.locator("button[aria-label='Generar código de registro']").click()
    code = page.locator("[data-testid=registration-code]").inner_text()
    assert CODE.match(code), code
    expect(page.locator(".modal")).to_contain_text("Sirve una sola vez")
    page.click("text=Listo")

    expect(row).to_contain_text("Código pendiente")
    expect(row).to_contain_text(code[-4:])  # la pista
    assert code not in page.content()  # el código completo ya no aparece en ningún lado

    row.locator("button[aria-label='Revocar código']").click()
    page.click(".modal .btn-danger")
    expect(row).to_contain_text("Sin vincular")


# --- CA-7: anti-XSS ---


def test_ca7_names_are_rendered_as_text_not_html(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/empleados.html")
    page.evaluate("window.__xss = false")

    create_employee(page, first='<img src=x onerror="window.__xss=true">', last="Prueba", internal_id="E2E-XSS")

    expect(page.locator("tr", has_text="E2E-XSS")).to_contain_text('<img src=x onerror="window.__xss=true">')
    assert page.evaluate("window.__xss") is False
    assert page.locator("tbody img").count() == 0


# --- CA-8: celular ---


def test_ca8_mobile_has_no_horizontal_scroll(page: Page, credentials: Credentials) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    login(page, credentials, "/empleados.html")
    create_employee(page)

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.locator(".sidebar")).not_to_be_in_viewport()
    page.click("button[aria-label='Abrir menú']")
    expect(page.locator(".sidebar")).to_be_in_viewport()
