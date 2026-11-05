"""Paso 8.3: informe mensual automático para el WF7 (CM-07 en n8n)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AdminUser, Company, Employee, Report
from app.models.enums import ExpenseStatus
from app.services import catalog, expenses, reports
from app.services.audit import Actor
from tests.test_reports import api_storage, storage  # noqa: F401 (fixtures)

ADMIN_CHAT = -100777
OCTOBER = date(2026, 10, 1)


@pytest.fixture
def internal() -> dict[str, str]:
    return {"X-Internal-Token": get_settings().internal_api_token.get_secret_value()}


@pytest.fixture
def admin_chat(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_telegram_chat_id", ADMIN_CHAT)


@pytest.fixture
def october(db: Session, company: Company, employee: Employee, admin: AdminUser) -> None:
    catalog.ensure_default_categories(db, company.id)
    actor = Actor.from_admin(admin)
    for day, total, status in ((date(2026, 10, 1), "100000", ExpenseStatus.APPROVED),
                               (date(2026, 10, 31), "50000", ExpenseStatus.SUBMITTED),
                               (date(2026, 11, 1), "999000", ExpenseStatus.SUBMITTED),  # ya es noviembre
                               (date(2026, 9, 30), "888000", ExpenseStatus.SUBMITTED)):  # aún era septiembre
        expense = expenses.create_expense(db, employee, {"description": "Gasto", "total": Decimal(total),
                                                         "issue_date": day}, actor=actor)
        expense.status = status
    db.flush()


def test_previous_month_and_label() -> None:
    assert reports.previous_month(date(2026, 11, 1)) == date(2026, 10, 1)
    assert reports.previous_month(date(2026, 1, 15)) == date(2025, 12, 1)
    assert reports.month_label(date(2026, 10, 1)) == "octubre de 2026"


def test_monthly_report_covers_the_whole_month_and_is_not_duplicated(db: Session, company: Company, october,
                                                                     storage) -> None:  # noqa: F811
    report = reports.monthly_report(db, storage, company.id, OCTOBER)

    assert (report.filters["date_from"], report.filters["date_to"]) == ("2026-10-01", "2026-10-31")
    assert report.filters["monthly"] == "2026-10"
    assert report.summary["expenses"] == 2 and report.summary["total"] == "150000.00"
    assert report.generated_by_id is None

    again = reports.monthly_report(db, storage, company.id, OCTOBER)
    assert again.id == report.id
    assert len(db.scalars(select(Report)).all()) == 1


def test_february_ends_on_its_last_day(db: Session, company: Company, storage) -> None:  # noqa: F811
    assert reports.monthly_report(db, storage, company.id, date(2028, 2, 1)).filters["date_to"] == "2028-02-29"


def test_database_blocks_a_second_monthly_report(db: Session, company: Company, storage) -> None:  # noqa: F811
    """El índice único protege aunque dos ejecuciones corran al mismo tiempo."""
    from sqlalchemy.exc import IntegrityError

    reports.monthly_report(db, storage, company.id, OCTOBER)
    duplicate = Report(company_id=company.id, filters={"monthly": "2026-10", "date_from": "x"},
                       storage_key="reports/x/2026/10/dup.pdf")
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(duplicate)
        db.flush()


# --- Endpoints internos ---


def test_without_admin_chat_nothing_is_generated(client, internal, db: Session, api_storage) -> None:  # noqa: F811
    body = client.post("/api/internal/reports/monthly", headers=internal, params={"month": "2026-10"}).json()

    assert body["status"] == "skipped" and "ADMIN_TELEGRAM_CHAT_ID" in body["reason"]
    assert db.scalars(select(Report)).all() == []


def test_monthly_flow_through_api(client, internal, db: Session, company: Company, october, admin_chat,
                                  api_storage) -> None:  # noqa: F811
    body = client.post("/api/internal/reports/monthly", headers=internal, params={"month": "2026-10"}).json()

    assert body["status"] == "ready" and body["month"] == "2026-10"
    (delivery,) = body["deliveries"]
    assert delivery["chat_id"] == ADMIN_CHAT and delivery["already_sent"] is False
    assert "octubre de 2026" in delivery["caption"] and "$150.000" in delivery["caption"]

    pdf = client.get(delivery["file_path"], headers=internal)
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    assert pdf.headers["content-disposition"] == 'attachment; filename="informe-2026-10.pdf"'

    result = client.post(f"/api/internal/reports/{delivery['report_id']}/delivery", headers=internal, json={"ok": True})
    assert result.json() == {"id": delivery["report_id"], "sent": True}

    # Si n8n vuelve a correr ese mes, no se reenvía.
    again = client.post("/api/internal/reports/monthly", headers=internal, params={"month": "2026-10"}).json()
    assert again["deliveries"][0]["already_sent"] is True
    assert again["deliveries"][0]["report_id"] == delivery["report_id"]


def test_failed_delivery_is_recorded_and_retried(client, internal, db: Session, october, admin_chat,
                                                 api_storage) -> None:  # noqa: F811
    delivery = client.post("/api/internal/reports/monthly", headers=internal,
                           params={"month": "2026-10"}).json()["deliveries"][0]

    client.post(f"/api/internal/reports/{delivery['report_id']}/delivery", headers=internal,
                json={"ok": False, "error_code": 400, "description": "Bad Request: chat not found"})

    report = db.get(Report, delivery["report_id"])
    db.refresh(report)
    assert report.summary["telegram_error"] == "400 Bad Request: chat not found"
    # Como no llegó, la siguiente ejecución lo vuelve a intentar.
    again = client.post("/api/internal/reports/monthly", headers=internal, params={"month": "2026-10"}).json()
    assert again["deliveries"][0]["already_sent"] is False


def test_caption_escapes_company_name(client, internal, db: Session, company: Company, admin_chat,
                                      api_storage) -> None:  # noqa: F811
    company.name = "<b>Mi</b> & Cía"
    db.flush()
    caption = client.post("/api/internal/reports/monthly", headers=internal,
                          params={"month": "2026-10"}).json()["deliveries"][0]["caption"]
    assert "&lt;b&gt;Mi&lt;/b&gt; &amp; Cía" in caption


@pytest.mark.parametrize("path", ["/api/internal/reports/monthly",
                                  "/api/internal/reports/00000000-0000-0000-0000-000000000000/delivery"])
def test_internal_endpoints_require_token(client, path) -> None:
    assert client.post(path, json={"ok": True}, headers={"X-Internal-Token": "falso"}).status_code in (401, 403)
    assert client.get("/api/internal/reports/00000000-0000-0000-0000-000000000000/file",
                      headers={"X-Internal-Token": "falso"}).status_code in (401, 403)


def test_invalid_month_is_rejected(client, internal) -> None:
    assert client.post("/api/internal/reports/monthly", headers=internal, params={"month": "2026-13"}).status_code == 422
