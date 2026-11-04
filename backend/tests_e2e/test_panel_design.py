"""Rediseño: portada con escaneo, fuente propia y fechas correctas."""

import httpx
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import text

from app.db.session import engine
from tests_e2e.conftest import BASE_URL, Credentials, login


def test_scan_scene_ends_verified(page: Page) -> None:
    page.goto(f"{BASE_URL}/login.html")

    expect(page.locator(".rc-stamp")).to_have_css("opacity", "1", timeout=6000)
    expect(page.locator(".receipt")).to_have_attribute("role", "img")
    assert "Verificado" in page.locator(".rc-stamp").text_content()  # inner_text no aplica a SVG


def test_reduced_motion_shows_final_state_at_once(browser: Browser) -> None:
    context = browser.new_context(reduced_motion="reduce")
    page = context.new_page()
    page.goto(f"{BASE_URL}/login.html")
    page.wait_for_selector(".receipt")

    assert page.eval_on_selector(".rc-stamp", "el => getComputedStyle(el).opacity") == "1"
    assert page.eval_on_selector(".rc-beam", "el => getComputedStyle(el).display") == "none"
    context.close()


def test_self_hosted_font_is_used(page: Page) -> None:
    page.goto(f"{BASE_URL}/login.html")
    page.wait_for_selector("h1")
    page.evaluate("document.fonts.ready")

    assert page.evaluate("[...document.fonts].some(f => f.family.includes('Archivo') && f.status === 'loaded')")


def test_invoice_date_is_not_shifted_by_timezone(page: Page, credentials: Credentials) -> None:
    """Regresión: '2026-11-03' se mostraba como '02 nov' (medianoche UTC = día anterior en Bogotá)."""
    with httpx.Client(base_url=f"{BASE_URL}/api/v1") as api:
        headers = {"X-CSRF-Token": api.post("/auth/login", json={
            "email": credentials.admin_email, "password": credentials.admin_initial_password}).json()["csrf_token"]}
        employee = api.post("/employees", json={"first_name": "Fecha", "last_name": "Prueba",
                                                "internal_id": "E2E-FECHA"}, headers=headers).json()
        expense = api.post("/expenses", json={"employee_id": employee["id"], "description": "E2E fecha",
                                              "total": "1000", "issue_date": "2026-11-03"}, headers=headers).json()
        try:
            login(page, credentials)
            row = page.locator("tr", has_text="E2E fecha")
            expect(row).to_contain_text("03")
            expect(row).not_to_contain_text("02 de nov")
        finally:
            # Los gastos solo se borran de forma lógica, así que el empleado quedaría con
            # registros y la API no permitiría eliminarlo: se limpia directo en la BD de desarrollo.
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM expenses WHERE id = :id"), {"id": expense["id"]})
                conn.execute(text("DELETE FROM employees WHERE id = :id"), {"id": employee["id"]})
