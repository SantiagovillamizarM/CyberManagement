"""Paso 8.2 en Edge real: informes (filtros, vista previa sin descarga automática, descarga e historial).

Usa la base de DESARROLLO y borra todo lo que crea (empleado E2E-*, sus gastos y los informes generados).
"""

import json
import re
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import text

from app.core.config import PROJECT_ROOT
from app.db.session import engine
from app.utils.dates import local_today
from tests_e2e.conftest import BASE_URL, Credentials, login
from tests_e2e.test_panel_viaticos import auditor  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def cleanup_reports() -> Iterator[None]:
    """Borra los informes que genere cada prueba (filas, auditoría y archivos)."""
    with engine.connect() as c:
        before = {str(i) for i in c.execute(text("SELECT id FROM reports")).scalars()}
    yield
    with engine.begin() as c:
        new = [str(i) for i in c.execute(text("SELECT id FROM reports")).scalars() if str(i) not in before]
        keys = c.execute(text("SELECT storage_key FROM reports WHERE id::text = ANY(:i)"), {"i": new}).scalars().all()
        c.execute(text("DELETE FROM audit_log WHERE entity_type = 'report' AND entity_id = ANY(:i)"), {"i": new})
        c.execute(text("DELETE FROM reports WHERE id::text = ANY(:i)"), {"i": new})
    for key in keys:
        (PROJECT_ROOT / "storage" / key).unlink(missing_ok=True)


@pytest.fixture
def employee(credentials: Credentials) -> Iterator[dict]:
    with httpx.Client(base_url=f"{BASE_URL}/api/v1") as api:
        api.headers["X-CSRF-Token"] = api.post("/auth/login", json={
            "email": credentials.admin_email, "password": credentials.admin_initial_password}).json()["csrf_token"]
        employee = api.post("/employees", json={"first_name": "Informe", "last_name": "Prueba",
                                                "internal_id": "E2E-INFORME"}).json()
        expense = api.post("/expenses", json={"employee_id": employee["id"], "description": "E2E informe",
                                              "total": "123000", "issue_date": local_today().isoformat(),
                                              "payment_source": "own_money"}).json()
        api.post(f"/expenses/{expense['id']}/submit")
    yield employee
    with engine.begin() as c:
        e = {"e": employee["id"]}
        c.execute(text("DELETE FROM audit_log WHERE entity_id IN (:e, :x)"), {"e": employee["id"], "x": expense["id"]})
        c.execute(text("DELETE FROM expenses WHERE employee_id = :e"), e)
        c.execute(text("DELETE FROM employees WHERE id = :e"), e)


def generate(page: Page) -> dict:
    with page.expect_response(lambda r: r.url.endswith("/api/v1/reports") and r.request.method == "POST") as info:
        page.click("button:has-text('Generar informe')")
    response = info.value
    assert response.status == 201, response.text()
    return response.json()


def test_preview_is_shown_and_nothing_is_downloaded(page: Page, credentials: Credentials, employee: dict) -> None:
    downloads = []
    page.on("download", lambda d: downloads.append(d))
    login(page, credentials, "/informes.html")
    expect(page.locator(".rp-empty")).to_contain_text("No se descarga hasta que tú lo decidas")

    report = generate(page)

    frame = page.locator("iframe.rp-frame")
    expect(frame).to_have_attribute("src", f"/api/v1/reports/{report['id']}/file#view=FitH")
    expect(page.locator(".rp-facts")).to_contain_text("facturas")
    page.wait_for_timeout(1500)
    assert downloads == []  # requerimiento 8: primero se ve, se descarga solo si se pide
    assert page.url.endswith(f"/informes.html?id={report['id']}")

    pdf = page.request.get(f"{BASE_URL}{report['file_url']}")
    assert pdf.headers["content-type"] == "application/pdf" and pdf.headers["content-disposition"].startswith("inline")
    assert pdf.body()[:5] == b"%PDF-"


def test_download_button_downloads(page: Page, credentials: Credentials, employee: dict) -> None:
    login(page, credentials, "/informes.html")
    report = generate(page)

    link = page.locator(".rp-actions a:has-text('Descargar PDF')")
    expect(link).to_have_attribute("href", f"{report['file_url']}?download=true")
    with page.expect_download() as info:
        link.click()
    assert info.value.suggested_filename.startswith("informe-") and info.value.suggested_filename.endswith(".pdf")


def test_filters_are_sent_and_shown_in_history(page: Page, credentials: Credentials, employee: dict) -> None:
    login(page, credentials, "/informes.html")
    page.select_option("#f-employee_id", label="Informe Prueba")
    page.uncheck("input[name=include_photos]")
    page.check("input[name=statuses][value=rejected]")

    with page.expect_request(lambda r: r.url.endswith("/api/v1/reports") and r.method == "POST") as info:
        page.click("button:has-text('Generar informe')")
    body = json.loads(info.value.post_data)
    assert body["employee_id"] == employee["id"] and body["include_photos"] is False
    assert set(body["statuses"]) == {"submitted", "in_review", "approved", "closed", "rejected"}

    row = page.locator(".card:has-text('Informes anteriores') tbody tr").first
    expect(row).to_contain_text("Informe Prueba")
    expect(row).to_contain_text("Rechazado")
    expect(row).to_contain_text("sin fotos")
    expect(page.locator(".rp-facts")).to_contain_text("$ 123.000")


def test_presets_and_validation(page: Page, credentials: Credentials) -> None:
    login(page, credentials, "/informes.html")
    today = local_today()

    page.click(".chip:has-text('Este año')")
    expect(page.locator("#f-date_from")).to_have_value(f"{today.year}-01-01")
    page.click(".chip:has-text('Mes anterior')")
    first_of_month = today.replace(day=1)
    expect(page.locator("#f-date_to")).to_have_value(date.fromordinal(first_of_month.toordinal() - 1).isoformat())

    requests = []
    page.on("request", lambda r: r.method == "POST" and "/reports" in r.url and requests.append(r))
    page.fill("#f-date_from", today.isoformat())
    page.fill("#f-date_to", today.replace(day=1).isoformat() if today.day > 1 else "2000-01-01")
    page.click("button:has-text('Generar informe')")
    expect(page.locator(".rp-form .form-error")).to_have_text("La fecha final no puede ser anterior a la inicial.")

    page.click(".chip:has-text('Este mes')")
    for box in page.locator("input[name=statuses]").all():
        box.uncheck()
    page.click("button:has-text('Generar informe')")
    expect(page.locator(".rp-form .form-error")).to_have_text("Elige al menos un estado de factura.")
    assert requests == []  # los errores se explican sin llamar al servidor


def test_deep_link_opens_a_previous_report(page: Page, credentials: Credentials, employee: dict) -> None:
    login(page, credentials, "/informes.html")
    report = generate(page)

    page.goto(f"{BASE_URL}/informes.html?id={report['id']}")

    expect(page.locator("iframe.rp-frame")).to_have_attribute("src", re.compile(report["id"]))


def test_auditor_can_generate_reports(page: Page, employee: dict, auditor) -> None:  # noqa: F811
    email, password = auditor
    page.goto(f"{BASE_URL}/login.html?next=/informes.html")
    page.fill("#email", email)
    page.fill("#password", password)
    page.click("button[type=submit]")
    page.wait_for_url(f"{BASE_URL}/informes.html")

    report = generate(page)
    assert report["generated_by"] == "Auditora E2E"


def test_mobile_has_no_horizontal_overflow(page: Page, credentials: Credentials, employee: dict) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    login(page, credentials, "/informes.html")
    generate(page)
    expect(page.locator("iframe.rp-frame")).to_be_visible()

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
