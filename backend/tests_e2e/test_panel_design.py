"""Rediseño: portada con carrusel, fuente propia, fechas correctas y gráficos del dashboard."""

from datetime import date

import httpx
from playwright.sync_api import Browser, Page, expect
from sqlalchemy import text

from app.db.session import engine
from tests_e2e.conftest import BASE_URL, Credentials, login, wait_for_entrance


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


# --- Carrusel de la portada ---


def test_carousel_shows_four_features_and_advances(page: Page) -> None:
    page.goto(f"{BASE_URL}/login.html")
    page.wait_for_selector(".carousel-slide.is-active .scene")

    titles = page.eval_on_selector_all(".carousel-caption strong", "els => els.map(e => e.textContent)")
    assert titles == ["Escanea facturas", "Guarda cada dato", "Viáticos al día", "Todo por Telegram"]
    # Margen amplio: depende del reloj real (6,5 s por escena) y el equipo puede estar ocupado.
    expect(page.locator(".carousel-caption.is-active strong")).to_have_text("Guarda cada dato", timeout=13000)


def test_carousel_pauses_while_hovered(page: Page) -> None:
    page.goto(f"{BASE_URL}/login.html")
    page.wait_for_selector(".carousel-stage")
    page.hover(".carousel-stage")

    page.wait_for_timeout(7500)  # más que la duración de una escena (6,5 s)

    expect(page.locator(".carousel-caption.is-active strong")).to_have_text("Escanea facturas")


def test_ambient_sparks_are_spread_without_inline_styles(page: Page) -> None:
    """Regresión: las chispas usaban style="" en línea, que la CSP bloquea (quedaban amontonadas)."""
    page.goto(f"{BASE_URL}/login.html")
    page.wait_for_selector(".ambient-spark")

    lefts = page.eval_on_selector_all(".ambient-spark", "els => els.map(e => Math.round(e.getBoundingClientRect().left))")
    assert len(set(lefts)) == len(lefts) == 8
    assert page.locator(".ambient-spark[style]").count() == 0  # su posición viene de clases CSS


# --- Gráficos del dashboard ---


def test_dashboard_charts_render_and_read_values(page: Page, credentials: Credentials) -> None:
    with httpx.Client(base_url=f"{BASE_URL}/api/v1") as api:
        headers = {"X-CSRF-Token": api.post("/auth/login", json={
            "email": credentials.admin_email, "password": credentials.admin_initial_password}).json()["csrf_token"]}
        employee = api.post("/employees", json={"first_name": "Gráfico", "last_name": "Prueba",
                                                "internal_id": "E2E-CHART"}, headers=headers).json()
        category = api.get("/categories").json()[0]
        for n, total in enumerate(["40000", "75000"]):
            expense = api.post("/expenses", json={"employee_id": employee["id"], "description": f"E2E gráfico {n}",
                                                  "total": total, "issue_date": date.today().isoformat(),
                                                  "category_id": category["id"]}, headers=headers).json()
            api.post(f"/expenses/{expense['id']}/submit", headers=headers)
        try:
            login(page, credentials)
            expect(page.locator(".area-plot svg .ac-line")).to_have_count(1)
            expect(page.locator(".seg-block").first).to_be_visible()
            expect(page.locator(".rank-row", has_text=category["name"])).to_be_visible()

            wait_for_entrance(page)  # medir con el contenido quieto
            box = page.locator(".area-plot").bounding_box()
            page.mouse.move(box["x"] + box["width"] - 70, box["y"] + box["height"] / 2)  # el día de hoy
            expect(page.locator(".chart-tip.is-visible strong")).to_contain_text("$")

            # Cada gráfico tiene su tabla equivalente para lectores de pantalla.
            assert page.locator(".area-chart table.sr-only tbody tr").count() == 30
        finally:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM expenses WHERE employee_id = :id"), {"id": employee["id"]})
                conn.execute(text("DELETE FROM employees WHERE id = :id"), {"id": employee["id"]})


def test_saved_badge_text_fits_inside_its_pill(page: Page) -> None:
    """Regresión: "Guardado con historial" se salía por la derecha de la pastilla verde."""
    page.goto(f"{BASE_URL}/login.html")
    page.click(".carousel-dot:nth-child(2)")  # escena "Guarda cada dato"
    page.wait_for_selector(".vs-badge-text", state="attached")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(300)

    margins = page.eval_on_selector(".vs-badge-text", """t => {
        const r = t.closest('g').querySelector('rect');
        const left = Number(t.getAttribute('x')) - Number(r.getAttribute('x'));
        const right = Number(r.getAttribute('x')) + Number(r.getAttribute('width'))
          - Number(t.getAttribute('x')) - t.getComputedTextLength();
        return [left, right];
    }""")
    assert margins[1] >= 12, f"el texto se sale o queda pegado al borde: {margins}"


def test_dashboard_uses_server_stats(page: Page, credentials: Credentials) -> None:
    """Paso 7.4: el navegador ya no descarga 200 gastos para sumarlos; las cifras vienen del servidor."""
    with httpx.Client(base_url=f"{BASE_URL}/api/v1") as api:
        headers = {"X-CSRF-Token": api.post("/auth/login", json={
            "email": credentials.admin_email, "password": credentials.admin_initial_password}).json()["csrf_token"]}
        employee = api.post("/employees", json={"first_name": "Estadística", "last_name": "Prueba",
                                                "internal_id": "E2E-STATS"}, headers=headers).json()
        viatico = api.post("/viaticos", json={"employee_id": employee["id"], "initial_amount": "300000",
                                              "purpose": "E2E estadísticas"}, headers=headers).json()
        expense = api.post("/expenses", json={"employee_id": employee["id"], "viatico_id": viatico["id"],
                                              "description": "E2E stats", "total": "120000",
                                              "issue_date": date.today().isoformat(), "payment_source": "viatico"},
                           headers=headers).json()
        api.post(f"/expenses/{expense['id']}/submit", headers=headers)
    requests: list[str] = []
    page.on("request", lambda r: requests.append(r.url))
    try:
        login(page, credentials)
        expect(page.locator(".rank-row", has_text="Estadística Prueba")).to_be_visible()
        wait_for_entrance(page)
        viaticos = page.locator(".dash-viaticos")
        expect(viaticos).to_contain_text("Viáticos entregados")
        expect(viaticos).to_contain_text("Saldo restante")
        expect(page.locator(".vt-row-link", has_text="E2E estadísticas")).to_have_attribute(
            "href", f"/viaticos.html?id={viatico['id']}")

        assert any("/api/v1/stats/dashboard" in url for url in requests)
        assert not any("/api/v1/expenses?limit=200" in url for url in requests)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM audit_log WHERE entity_id IN (:e, :x, :v)"),
                         {"e": employee["id"], "x": expense["id"], "v": viatico["id"]})
            conn.execute(text("DELETE FROM notifications WHERE employee_id = :id"), {"id": employee["id"]})
            conn.execute(text("DELETE FROM expenses WHERE employee_id = :id"), {"id": employee["id"]})
            conn.execute(text("DELETE FROM viaticos WHERE employee_id = :id"), {"id": employee["id"]})
            conn.execute(text("DELETE FROM employees WHERE id = :id"), {"id": employee["id"]})
