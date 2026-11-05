"""Paso 7.3 en Edge real: viáticos (asignar, movimientos, liquidación y cierre).

Usa la base de DESARROLLO y borra todo lo que crea. El empleado de prueba queda "vinculado" a un
chat de Telegram inexistente: así se crean los avisos sin escribirle a nadie.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import PROJECT_ROOT
from app.core.security import hash_password
from app.db.session import engine
from app.models import AdminUser, Company
from app.models.enums import AdminRole
from tests_e2e.conftest import BASE_URL, Credentials, login

MODAL = ".modal-backdrop:not(.is-closing) .modal"
FAKE_CHAT = 999000777


@dataclass
class Seed:
    employee: dict
    medellin: dict  # 500.000 entregados; una factura de 68.000 sin revisar (impide cerrar)
    cali: dict  # 150.000 entregados; 171.000 aprobados (gastó de más)
    pending_expense: dict


@pytest.fixture
def seed(credentials: Credentials) -> Iterator[Seed]:
    with httpx.Client(base_url=f"{BASE_URL}/api/v1") as api:
        api.headers["X-CSRF-Token"] = api.post("/auth/login", json={
            "email": credentials.admin_email, "password": credentials.admin_initial_password}).json()["csrf_token"]
        employee = api.post("/employees", json={"first_name": "Viático", "last_name": "Prueba",
                                                "internal_id": "E2E-VIATICOS"}).json()

        def spend(viatico, description, total, approve=False):
            e = api.post("/expenses", json={"employee_id": employee["id"], "viatico_id": viatico["id"],
                                            "description": description, "total": total, "issue_date": "2026-11-03",
                                            "payment_source": "viatico"}).json()
            api.post(f"/expenses/{e['id']}/submit")
            if approve:
                api.post(f"/expenses/{e['id']}/approve")
            return e

        medellin = api.post("/viaticos", json={"employee_id": employee["id"], "initial_amount": "500000",
                                               "purpose": "E2E Viaje a Medellín"}).json()
        pending = spend(medellin, "E2E almuerzo", "68000")
        cali = api.post("/viaticos", json={"employee_id": employee["id"], "initial_amount": "150000",
                                           "purpose": "E2E Visita a Cali"}).json()
        spend(cali, "E2E taxis", "171000", approve=True)
    with engine.begin() as c:
        c.execute(text("UPDATE employees SET telegram_user_id = :t, telegram_chat_id = :t WHERE id = :e"),
                  {"t": FAKE_CHAT, "e": employee["id"]})
        c.execute(text("DELETE FROM notifications WHERE employee_id = :e"), {"e": employee["id"]})
    yield Seed(employee, medellin, cali, pending)

    with engine.begin() as c:
        e = {"e": employee["id"]}
        ids = [employee["id"], *[str(i) for i in c.execute(text("SELECT id FROM expenses WHERE employee_id = :e"), e).scalars()],
               *[str(i) for i in c.execute(text("SELECT id FROM viaticos WHERE employee_id = :e"), e).scalars()]]
        c.execute(text("DELETE FROM audit_log WHERE entity_id = ANY(:i)"), {"i": [str(i) for i in ids]})
        c.execute(text("DELETE FROM expenses WHERE employee_id = :e"), e)
        c.execute(text("DELETE FROM viaticos WHERE employee_id = :e"), e)
        c.execute(text("DELETE FROM employees WHERE id = :e"), e)  # sus avisos se borran en cascada


def top(page: Page):
    """La ventana de arriba (el formulario se abre encima del detalle)."""
    return page.locator(MODAL).last


def notifications(employee_id: str) -> list[str]:
    with engine.connect() as c:
        return c.execute(text("SELECT kind FROM notifications WHERE employee_id = :e ORDER BY created_at"),
                         {"e": employee_id}).scalars().all()


def open_page(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/viaticos.html")
    page.select_option("select[aria-label='Filtrar por empleado']", label="Viático Prueba")
    expect(page.locator(".vt-card")).to_have_count(2)


def open_card(page: Page, name: str) -> None:
    page.locator(".vt-card", has_text=name).click()
    expect(page.locator(MODAL + " .vt-breakdown")).to_be_visible()


def test_cards_show_balance_and_overspending(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_page(page, credentials)

    cali = page.locator(".vt-card", has_text="Cali")
    expect(cali).to_contain_text("-$ 21.000")
    expect(cali.locator(".vt-flag")).to_contain_text("Gastó más de lo entregado")
    expect(page.locator(".vt-card", has_text="Medellín")).to_contain_text("$ 432.000")
    expect(page.locator(".vt-card").first).to_contain_text("Cali")  # el más gastado primero
    expect(page.locator(".vt-summary")).to_contain_text("$ 650.000")  # entregado
    expect(page.locator(".vt-summary")).to_contain_text("1 con gasto de más")


def test_assign_viatico_notifies_employee(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_page(page, credentials)
    page.click("button:has-text('Asignar viático')")
    page.select_option("#f-employee_id", label="Viático Prueba")

    top(page).get_by_role("button", name="Asignar viático").click()
    expect(page.locator("#e-initial_amount")).to_have_text("Escribe el valor")

    page.fill("#f-initial_amount", "300.000")
    page.fill("#f-purpose", "E2E Capacitación Bogotá")
    top(page).get_by_role("button", name="Asignar viático").click()

    expect(page.locator(MODAL + " .modal-header h2")).to_have_text("E2E Capacitación Bogotá")
    expect(page.locator(MODAL + " .exp-total")).to_have_text("$ 300.000")
    assert notifications(seed.employee["id"]) == ["viatico_opened"]


def test_adjustment_requires_note_and_updates_balance(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_page(page, credentials)
    open_card(page, "Medellín")
    page.click(f"{MODAL} button:has-text('Registrar movimiento')")
    page.select_option("#f-type", "adjustment")
    page.fill("#f-amount", "-32.000")
    expect(page.locator(".vt-preview")).to_contain_text("$ 400.000")  # 432.000 − 32.000

    top(page).get_by_role("button", name="Registrar", exact=True).click()
    expect(page.locator("#e-note")).to_contain_text("necesita una nota")

    page.fill("#f-note", "Se descontó un anticipo duplicado")
    top(page).get_by_role("button", name="Registrar", exact=True).click()

    detail = page.locator(MODAL)
    expect(detail.locator(".exp-total")).to_have_text("$ 400.000")
    expect(detail.locator(".timeline li").first).to_contain_text("Ajuste")
    expect(detail.locator(".timeline li").first).to_contain_text("− $ 32.000")
    expect(detail.locator(".timeline li").first).to_contain_text("Se descontó un anticipo duplicado")
    assert notifications(seed.employee["id"]) == ["viatico_adjustment"]


def test_close_is_blocked_while_invoices_are_pending(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_page(page, credentials)
    open_card(page, "Medellín")

    blocked = page.locator(MODAL + " .vt-blocked")
    expect(blocked).to_contain_text("1 factura está sin aprobar ni rechazar")
    expect(page.locator(MODAL + " button:has-text('Cerrar viático')")).to_have_count(0)

    blocked.locator("a").click()
    page.wait_for_url(f"{BASE_URL}/gastos.html?viatico={seed.medellin['id']}")
    expect(page.locator(".notice-info")).to_contain_text("solo las facturas de un viático")
    expect(page.locator("tbody tr")).to_have_count(1)
    expect(page.locator("tbody tr")).to_contain_text("E2E almuerzo")


def test_close_shows_settlement_and_notifies(page: Page, credentials: Credentials, seed: Seed) -> None:
    open_page(page, credentials)
    open_card(page, "Cali")
    expect(page.locator(MODAL + " .notice-danger")).to_contain_text("La empresa le debe reembolsar $ 21.000")

    page.click(f"{MODAL} button:has-text('Cerrar viático')")
    expect(top(page)).to_contain_text("Liquidación: La empresa le debe reembolsar $ 21.000")
    top(page).get_by_role("button", name="Cerrar viático").click()

    detail = page.locator(MODAL)
    expect(detail.locator(".vt-hero")).to_contain_text("Cerrado")
    expect(detail).to_contain_text("Liquidación:")
    expect(detail.locator(".modal-footer")).to_have_count(0)  # cerrado: ya no hay acciones
    assert notifications(seed.employee["id"]) == ["viatico_closed"]


def test_mobile_has_no_horizontal_overflow(page: Page, credentials: Credentials, seed: Seed) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    open_page(page, credentials)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")

    open_card(page, "Medellín")
    assert page.locator(MODAL).evaluate("m => m.scrollWidth <= m.clientWidth")


# --- Auditor: solo lectura ---


@pytest.fixture
def auditor() -> Iterator[tuple[str, str]]:
    email, password = "e2e-auditor@cybermanagement.com", "auditoria-de-prueba-segura"
    with Session(engine) as db:
        company = db.query(Company).first()
        user = AdminUser(id=uuid.uuid4(), company_id=company.id, email=email, full_name="Auditora E2E",
                         password_hash=hash_password(password), role=AdminRole.AUDITOR)
        db.add(user)
        db.commit()
        user_id = user.id
    yield email, password
    with engine.begin() as c:
        # Primero lo que generó (informes): lo referencian a él.
        keys = c.execute(text("SELECT storage_key FROM reports WHERE generated_by_id = :i"), {"i": user_id}).scalars().all()
        c.execute(text("DELETE FROM reports WHERE generated_by_id = :i"), {"i": user_id})
        c.execute(text("DELETE FROM admin_sessions WHERE admin_user_id = :i"), {"i": user_id})
        c.execute(text("DELETE FROM audit_log WHERE actor_id = :i"), {"i": str(user_id)})
        c.execute(text("DELETE FROM admin_users WHERE id = :i"), {"i": user_id})
    for key in keys:
        (PROJECT_ROOT / "storage" / key).unlink(missing_ok=True)


def test_auditor_only_reads(page: Page, seed: Seed, auditor) -> None:
    email, password = auditor
    page.goto(f"{BASE_URL}/login.html?next=/viaticos.html")
    page.fill("#email", email)
    page.fill("#password", password)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE_URL}/viaticos.html")
    page.select_option("select[aria-label='Filtrar por empleado']", label="Viático Prueba")

    expect(page.locator("button:has-text('Asignar viático')")).to_have_count(0)
    open_card(page, "Medellín")
    expect(page.locator(MODAL + " .modal-footer")).to_have_count(0)  # ni movimientos ni cierre

    page.goto(f"{BASE_URL}/gastos.html?id={seed.pending_expense['id']}")
    expect(page.locator(MODAL + " .exp-total")).to_have_text("$ 68.000")
    expect(page.locator(MODAL + " .modal-footer")).to_have_count(0)  # ni aprobar ni rechazar
    expect(page.locator(MODAL + " button:has-text('Volver a leer')")).to_have_count(0)
