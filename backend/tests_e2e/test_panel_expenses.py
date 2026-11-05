"""Paso 7.2 en Edge real: facturas y gastos (lista, detalle con foto, IA, historial, aprobar y rechazar).

Usa la base de DESARROLLO y borra todo lo que crea. La "lectura de IA" se inserta directo en la
BD (no se llama a Gemini ni se gasta cuota).
"""

import io
import json
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from PIL import Image
from playwright.sync_api import Page, expect
from sqlalchemy import text

from app.core.config import PROJECT_ROOT
from app.db.session import engine
from tests_e2e.conftest import BASE_URL, Credentials, login

MODAL = ".modal-backdrop:not(.is-closing) .modal"
NIT = "900.481.516-6"
FAKE_CHAT = 999000666  # chat inexistente: el aviso de Telegram fallará sin molestar a nadie


@dataclass
class Seed:
    employee: dict
    review: dict  # enviado, con foto y lectura de IA (el total se corrigió a mano)
    other: dict  # enviado, sin foto


def jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (300, 420), (240, 238, 230)).save(buffer, "JPEG")
    return buffer.getvalue()


@pytest.fixture
def seed(credentials: Credentials) -> Iterator[Seed]:
    with httpx.Client(base_url=f"{BASE_URL}/api/v1") as api:
        api.headers["X-CSRF-Token"] = api.post("/auth/login", json={
            "email": credentials.admin_email, "password": credentials.admin_initial_password}).json()["csrf_token"]
        employee = api.post("/employees", json={"first_name": "Revisión", "last_name": "Prueba",
                                                "internal_id": "E2E-GASTOS"}).json()
        review = api.post("/expenses", json={
            "employee_id": employee["id"], "description": "E2E almuerzo con cliente", "total": "70000",
            "subtotal": "62963", "tax_total": "5037", "issue_date": "2026-11-03", "invoice_number": "FEV-E2E-1",
            "supplier_nit": NIT, "supplier_name": "Restaurante E2E", "payment_source": "own_money",
            "items": [{"description": "Bandeja paisa", "quantity": "2", "unit_price": "28000", "line_total": "56000"}],
        }).json()
        upload = api.post(f"/expenses/{review['id']}/documents", files={"file": ("f.jpg", jpeg(), "image/jpeg")}).json()
        api.post(f"/expenses/{review['id']}/submit")
        other = api.post("/expenses", json={"employee_id": employee["id"], "description": "E2E taxi aeropuerto",
                                            "total": "25000", "issue_date": "2026-11-02",
                                            "payment_source": "own_money"}).json()
        api.post(f"/expenses/{other['id']}/submit")

    with engine.begin() as c:
        # Vinculado a un chat inexistente: así se crea el aviso de Telegram al aprobar o rechazar.
        c.execute(text("UPDATE employees SET telegram_user_id = :t, telegram_chat_id = :t WHERE id = :e"),
                  {"t": FAKE_CHAT, "e": employee["id"]})
        parsed = {"total": "68000.00", "subtotal": "62963.00", "tax_total": "5037.00", "issue_date": "2026-11-03",
                  "supplier_nit": "900481516-6", "invoice_number": "FEV-E2E-1", "warnings": []}
        c.execute(text(
            "INSERT INTO ai_extractions (id, document_id, model, prompt_version, outcome, is_invoice, confidence, parsed) "
            "VALUES (:id, :doc, 'gemini-2.5-flash', 'e2e', 'extracted', true, 0.93, CAST(:parsed AS jsonb))"),
            {"id": uuid.uuid4(), "doc": upload["document"]["id"], "parsed": json.dumps(parsed)})
    yield Seed(employee, review, other)

    with engine.begin() as c:
        e = {"e": employee["id"]}
        keys = c.execute(text("SELECT storage_key FROM documents WHERE employee_id = :e"), e).scalars().all()
        ids = [employee["id"], review["id"], other["id"],
               *[str(i) for i in c.execute(text("SELECT id FROM documents WHERE employee_id = :e"), e).scalars()]]
        c.execute(text("DELETE FROM audit_log WHERE entity_id = ANY(:i)"), {"i": [str(i) for i in ids]})
        c.execute(text("DELETE FROM documents WHERE employee_id = :e"), e)
        c.execute(text("DELETE FROM expenses WHERE employee_id = :e"), e)
        c.execute(text("DELETE FROM suppliers WHERE nit = '900481516'"))
        c.execute(text("DELETE FROM employees WHERE id = :e"), e)  # sus avisos se borran en cascada
    for key in keys:
        (PROJECT_ROOT / "storage" / key).unlink(missing_ok=True)


def open_list(page: Page, credentials: Credentials, suffix: str = "") -> None:
    login(page, credentials, "/gastos.html" + suffix)
    page.fill("input[type=search]", "E2E")
    expect(page.locator("tbody tr")).to_have_count(2)


def open_review(page: Page) -> None:
    page.locator("tbody tr", has_text="E2E almuerzo").click()
    expect(page.locator(MODAL)).to_contain_text("$ 70.000")


def notifications(employee_id: str) -> list[str]:
    with engine.connect() as c:
        return c.execute(text("SELECT kind FROM notifications WHERE employee_id = :e ORDER BY created_at"),
                         {"e": employee_id}).scalars().all()


# --- Lista y filtros ---


def test_list_search_and_quick_filters(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_list(page, credentials)

    row = page.locator("tbody tr", has_text="E2E almuerzo")
    expect(row).to_contain_text("Restaurante E2E")
    expect(row).to_contain_text("NIT 900481516-6")
    expect(row).to_contain_text("Enviado")

    page.fill("input[type=search]", "aeropuerto")
    expect(page.locator("tbody tr")).to_have_count(1)
    page.fill("input[type=search]", "E2E")
    page.click(".chip:has-text('Aprobados')")
    expect(page.locator(".empty")).to_contain_text("No hay facturas con estos filtros")
    page.click(".chip:has-text('Por revisar')")
    expect(page.locator("tbody tr")).to_have_count(2)
    assert page.url.endswith("?status=review")


def test_nav_item_is_enabled(page: Page, credentials: Credentials) -> None:
    login(page, credentials)
    page.click(".nav a:has-text('Facturas')")
    page.wait_for_url(f"{BASE_URL}/gastos.html")
    expect(page.locator("h1")).to_have_text("Facturas y gastos")


# --- Detalle ---


def test_detail_shows_photo_ai_comparison_and_items(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_list(page, credentials)
    open_review(page)
    modal = page.locator(MODAL)

    image = modal.locator(".photo-frame img")
    expect(image).to_be_visible()
    page.wait_for_function("img => img.complete && img.naturalWidth > 0", arg=image.element_handle())
    expect(modal.locator(".kv")).to_contain_text("Restaurante E2E")
    expect(modal.locator(".kv")).to_contain_text("FEV-E2E-1")
    expect(modal).to_contain_text("Bandeja paisa")

    # La IA leyó $68.000 pero quedó registrado $70.000: se marca como corregido.
    total_row = modal.locator(".ai-compare tr", has_text="Valor total")
    expect(total_row).to_contain_text("$ 68.000")
    expect(total_row).to_contain_text("Corregido")
    expect(modal.locator(".ai-compare tr", has_text="NIT")).not_to_contain_text("Corregido")
    expect(modal.locator(".ai-meta")).to_contain_text("Confianza 93 %")
    expect(modal.locator(".timeline")).to_contain_text("lo envió a revisión")


def test_deep_link_opens_the_detail(page: Page, credentials: Credentials, seed: Seed) -> None:
    login(page, credentials, f"/gastos.html?id={seed.other['id']}")

    expect(page.locator(MODAL)).to_contain_text("$ 25.000")
    expect(page.locator(MODAL + " .photo-empty")).to_contain_text("Sin foto")
    page.keyboard.press("Escape")
    expect(page).to_have_url(re.compile(r"/gastos\.html$"))


# --- Aprobar, rechazar y corregir ---


def test_approve_notifies_employee(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_list(page, credentials)
    open_review(page)

    page.click(f"{MODAL} .btn-primary:has-text('Aprobar')")

    expect(page.locator(".toast")).to_contain_text("Se le avisará al empleado")
    expect(page.locator(MODAL + " .exp-badges")).to_contain_text("Aprobado")
    expect(page.locator(MODAL + " .modal-footer")).to_contain_text("Cerrar")  # siguiente paso posible
    expect(page.locator("tbody tr", has_text="E2E almuerzo")).to_contain_text("Aprobado")  # la lista se actualizó
    assert notifications(seed.employee["id"]) == ["expense_approved"]


def test_reject_requires_reason(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_list(page, credentials)
    open_review(page)
    page.click(f"{MODAL} .btn-danger:has-text('Rechazar')")

    page.click(f"{MODAL} .btn-danger:has-text('Rechazar gasto')")
    expect(page.locator("#e-reason")).to_contain_text("mínimo 5 caracteres")

    page.fill("#f-reason", "La factura no corresponde a la fecha del viaje")
    page.click(f"{MODAL} .btn-danger:has-text('Rechazar gasto')")

    detail = page.locator(MODAL)
    expect(detail.locator(".notice-danger")).to_contain_text("no corresponde a la fecha del viaje")
    expect(detail.locator(".timeline")).to_contain_text("lo rechazó")
    assert notifications(seed.employee["id"]) == ["expense_rejected"]


def test_correct_total_is_recorded_in_history(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_list(page, credentials)
    open_review(page)
    page.click(f"{MODAL} button:has-text('Corregir')")

    page.fill("#f-total", "68.000")  # se corrige al valor que leyó la IA
    page.click(f"{MODAL} button:has-text('Guardar cambios')")

    detail = page.locator(MODAL)
    expect(detail.locator(".exp-total")).to_have_text("$ 68.000")
    expect(detail.locator(".ai-compare tr", has_text="Valor total")).not_to_contain_text("Corregido")
    expect(detail.locator(".timeline li").first).to_contain_text("corrigió Valor total")
    expect(detail.locator(".timeline li").first).to_contain_text("$ 70.000")


def test_invalid_money_is_explained(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_list(page, credentials)
    open_review(page)
    page.click(f"{MODAL} button:has-text('Corregir')")

    page.fill("#f-total", "setenta mil")
    page.click(f"{MODAL} button:has-text('Guardar cambios')")

    expect(page.locator("#e-total")).to_contain_text("45.000")


def test_texts_are_rendered_safely(page: Page, credentials: Credentials, seed: Seed) -> None:
    with engine.begin() as c:
        c.execute(text("UPDATE expenses SET description = :d WHERE id = :i"),
                  {"d": '<img src=x onerror="window.__xss=true">', "i": seed.other["id"]})
    login(page, credentials, "/gastos.html")
    page.evaluate("window.__xss = false")
    page.fill("input[type=search]", "img src")
    expect(page.locator("tbody tr")).to_have_count(1)  # la búsqueda espera 300 ms antes de filtrar

    expect(page.locator("tbody tr")).to_contain_text('<img src=x onerror="window.__xss=true">')
    assert page.evaluate("window.__xss") is False


def test_mobile_detail_has_no_horizontal_scroll(page: Page, credentials: Credentials, seed: Seed) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    open_list(page, credentials)
    open_review(page)

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    modal_box = page.locator(MODAL).bounding_box()
    assert modal_box["width"] <= 390
    # Regresión: el contenido de la ventana se salía por la derecha (estados y avisos cortados).
    wide = page.locator(MODAL).evaluate("""m => { const r = m.getBoundingClientRect().right;
      return [...m.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > r + 1)
        .slice(0, 6).map(e => e.tagName + '.' + (e.className.baseVal ?? e.className) + ' ' + Math.round(e.getBoundingClientRect().right)); }""")
    assert page.locator(MODAL).evaluate("m => m.scrollWidth <= m.clientWidth"), wide
    for selector in (".exp-badges", ".exp-warnings li"):
        right = page.locator(f"{MODAL} {selector}").first.evaluate("e => e.getBoundingClientRect().right")
        assert right <= modal_box["x"] + modal_box["width"], selector
