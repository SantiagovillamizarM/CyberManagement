"""Paso 7.1: la API sirve el panel con cabeceras de seguridad (CA-1 y CA-7)."""

import pytest
from fastapi.testclient import TestClient

from app.main import PANEL_CSP, app

client = TestClient(app)


@pytest.mark.parametrize("path", ["/", "/login.html", "/empleados.html"])
def test_pages_are_served_with_csp(path: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["content-security-policy"] == PANEL_CSP
    assert response.headers["x-frame-options"] == "DENY"


def test_csp_forbids_inline_and_external_scripts() -> None:
    assert "script-src 'self'" in PANEL_CSP
    assert "unsafe-inline" not in PANEL_CSP
    assert "frame-ancestors 'none'" in PANEL_CSP


def test_api_keeps_priority_over_panel() -> None:
    response = client.get("/api/health")

    assert response.headers["content-type"] == "application/json"
    assert "content-security-policy" not in response.headers


@pytest.mark.parametrize("path", ["/../.env", "/%2e%2e/.env", "/assets/../../.env", "/..%2f..%2f.env"])
def test_files_outside_frontend_are_not_reachable(path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert "TELEGRAM_BOT_TOKEN" not in response.text


def test_pages_have_no_inline_scripts() -> None:
    """La CSP bloquearía cualquier <script> en línea: todos deben ser archivos."""
    for path in ["/", "/login.html", "/empleados.html"]:
        html = client.get(path).text
        assert "<script>" not in html
        assert 'type="module" src="/assets/js/pages/' in html
