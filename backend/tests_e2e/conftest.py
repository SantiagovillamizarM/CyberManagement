"""Pruebas de punta a punta del panel en un navegador real (Edge, vía Playwright).

Requisitos: la API corriendo (docker compose up -d) y Edge instalado.
Ejecutar (desde backend):  .venv\\Scripts\\python.exe -m pytest tests_e2e -v

Usan la base de DESARROLLO: todo lo que crean lo eliminan al final.
"""

from collections.abc import Iterator

import httpx
import pytest
from playwright.sync_api import Browser, Page, sync_playwright
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import PROJECT_ROOT

BASE_URL = "http://127.0.0.1:8000"
TEST_PREFIX = "E2E-"  # identificación interna de lo que crean estas pruebas


class Credentials(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")
    admin_email: str
    admin_initial_password: str


@pytest.fixture(scope="session")
def credentials() -> Credentials:
    return Credentials()


@pytest.fixture(scope="session", autouse=True)
def server_running() -> None:
    try:
        httpx.get(f"{BASE_URL}/api/health", timeout=3).raise_for_status()
    except httpx.HTTPError:
        pytest.skip("La API no está corriendo en 127.0.0.1:8000 (docker compose up -d)")


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(browser: Browser) -> Iterator[Page]:
    context = browser.new_context(viewport={"width": 1366, "height": 860}, locale="es-CO")
    page = context.new_page()
    page.js_errors = []  # errores de JavaScript o violaciones de CSP
    page.on("pageerror", lambda err: page.js_errors.append(str(err)))
    page.on("console", lambda m: m.type == "error" and "Content Security Policy" in m.text
            and page.js_errors.append(m.text))
    yield page
    context.close()
    assert page.js_errors == [], f"Errores en el navegador: {page.js_errors}"


def login(page: Page, credentials: Credentials, next_path: str = "/") -> None:
    page.goto(f"{BASE_URL}/login.html?next={next_path}")
    page.fill("#email", credentials.admin_email)
    page.fill("#password", credentials.admin_initial_password)
    page.click("button[type=submit]")
    # URL exacta: "/login.html?next=/empleados.html" también TERMINA en "/empleados.html".
    page.wait_for_url(BASE_URL + next_path)
    page.wait_for_selector(".content h1")


@pytest.fixture(autouse=True)
def cleanup(credentials: Credentials) -> Iterator[None]:
    """Elimina los empleados de prueba (E2E-*) antes y después de cada prueba."""

    def purge() -> None:
        with httpx.Client(base_url=f"{BASE_URL}/api/v1") as api:
            login_response = api.post("/auth/login", json={"email": credentials.admin_email,
                                                            "password": credentials.admin_initial_password})
            headers = {"X-CSRF-Token": login_response.json()["csrf_token"]}
            for employee in api.get("/employees", params={"search": TEST_PREFIX, "limit": 200}).json()["items"]:
                if employee["internal_id"].startswith(TEST_PREFIX):
                    api.delete(f"/employees/{employee['id']}", headers=headers)
            api.post("/auth/logout", headers=headers)

    purge()
    yield
    purge()


def wait_for_entrance(page: Page) -> None:
    """Espera a que terminen las animaciones de entrada (las finitas; el fondo animado es infinito).

    Mientras el contenido "sube" al entrar, su posición cambia: medir y mover el mouse en ese
    momento hace que el puntero caiga en otro lugar (era la prueba intermitente del dashboard).
    """
    page.wait_for_function("""() => document.getAnimations()
        .filter(a => a.effect && a.effect.getTiming().iterations !== Infinity)
        .every(a => a.playState !== 'running')""")

