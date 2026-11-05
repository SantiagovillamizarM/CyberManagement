"""Paso 7.4: estadísticas del dashboard calculadas en la base de datos (CA-1 a CA-4)."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import AdminUser, Company, Employee
from app.models.enums import AdminRole, EmployeeStatus, ExpenseStatus, MovementSource, MovementType
from app.services import catalog, expenses, stats, viaticos
from app.services.audit import Actor
from app.utils.dates import local_today

D = Decimal
BOGOTA = ZoneInfo("America/Bogota")
TODAY = local_today()
PREVIOUS_MONTH = (TODAY.replace(day=1) - timedelta(days=1)).replace(day=10)


@pytest.fixture
def actor(admin: AdminUser) -> Actor:
    return Actor.from_admin(admin)


@pytest.fixture
def second(db: Session, company: Company) -> Employee:
    other = Employee(company_id=company.id, first_name="Ana", last_name="Ruiz", internal_id="EMP-002")
    db.add(other)
    db.flush()
    return other


def spend(db, employee, actor, total, *, status=ExpenseStatus.SUBMITTED, day=TODAY, category=None, viatico=None,
          received_at=None):
    fields = {"description": "Gasto", "total": D(total), "issue_date": day}
    if category:
        fields["category_id"] = next(c.id for c in catalog.list_categories(db, employee.company_id) if c.name == category)
    if viatico:
        fields["viatico_id"] = viatico.id
    expense = expenses.create_expense(db, employee, fields, actor=actor)
    expense.status = status
    if received_at:
        expense.received_at = received_at
    db.flush()
    return expense


@pytest.fixture
def data(db: Session, company: Company, employee: Employee, second: Employee, actor: Actor):
    catalog.ensure_default_categories(db, company.id)
    spend(db, employee, actor, "100000", category="Alimentación")
    spend(db, employee, actor, "50000", status=ExpenseStatus.APPROVED, category="Transporte")
    spend(db, second, actor, "300000", status=ExpenseStatus.IN_REVIEW, category="Hospedaje")
    spend(db, second, actor, "20000", status=ExpenseStatus.CLOSED, day=PREVIOUS_MONTH, category="Transporte")
    # Lo que NO cuenta como gastado:
    spend(db, employee, actor, "999000", status=ExpenseStatus.REJECTED)
    spend(db, employee, actor, "888000", status=ExpenseStatus.DRAFT)
    deleted = spend(db, employee, actor, "777000")
    deleted.deleted_at = datetime.now(UTC)
    db.flush()


# --- CA-1 y CA-2: cifras y reglas ---


def test_month_totals_and_change(db: Session, company: Company, data) -> None:
    s = stats.dashboard(db, company.id)

    assert s.month_total == D("450000")  # 100.000 + 50.000 + 300.000 (sin rechazados, borradores ni eliminados)
    assert s.previous_month_total == D("20000")
    assert s.counted_total == D("470000") and s.counted_count == 4


def test_first_day_of_the_month_counts_only_in_this_month(db: Session, company: Company, employee: Employee,
                                                          actor: Actor) -> None:
    spend(db, employee, actor, "40000", day=TODAY.replace(day=1))
    spend(db, employee, actor, "15000", day=TODAY.replace(day=1) - timedelta(days=1))  # último día del mes anterior

    s = stats.dashboard(db, company.id)

    assert (s.month_total, s.previous_month_total) == (D("40000"), D("15000"))


def test_daily_series_has_30_days_ending_today(db: Session, company: Company, data) -> None:
    s = stats.dashboard(db, company.id)

    assert len(s.daily) == 30
    assert s.daily[-1] == (TODAY, D("450000"))
    assert s.daily[0][0] == TODAY - timedelta(days=29)
    assert sum(t for _, t in s.daily) == D("450000") + (D("20000") if PREVIOUS_MONTH >= TODAY - timedelta(days=29) else 0)


def test_day_without_invoice_date_uses_colombian_time(db: Session, company: Company, employee: Employee,
                                                      actor: Actor) -> None:
    """Recibido ayer a las 11:30 p. m. en Bogotá (= hoy 04:30 UTC): cuenta para AYER."""
    yesterday = TODAY - timedelta(days=1)
    late = datetime.combine(yesterday, time(23, 30), tzinfo=BOGOTA)
    expense = spend(db, employee, actor, "70000", received_at=late)
    expense.issue_date = None
    db.flush()

    daily = dict(stats.dashboard(db, company.id).daily)

    assert daily[yesterday] == D("70000")
    assert daily[TODAY] == D("0")


def test_status_breakdown_counts_everything_but_deleted(db: Session, company: Company, data) -> None:
    by_status = stats.dashboard(db, company.id).by_status

    assert by_status["submitted"] == (1, D("100000"))
    assert by_status["approved"] == (1, D("50000"))
    assert by_status["in_review"] == (1, D("300000"))
    assert by_status["rejected"] == (1, D("999000"))
    assert by_status["draft"] == (1, D("888000"))
    assert by_status["closed"] == (1, D("20000"))
    assert by_status["pending_completion"] == (0, D("0"))


def test_category_and_employee_rankings(db: Session, company: Company, employee: Employee, second: Employee,
                                        data) -> None:
    s = stats.dashboard(db, company.id)

    assert s.by_category == [("Hospedaje", D("300000")), ("Alimentación", D("100000")), ("Transporte", D("70000"))]
    assert [(name, count, total) for _, name, count, total in s.by_employee] == [
        ("Ana Ruiz", 2, D("320000")), (f"{employee.first_name} {employee.last_name}", 2, D("150000"))]


def test_employees_counts(db: Session, company: Company, employee: Employee, second: Employee) -> None:
    employee.telegram_user_id = 123
    second.status = EmployeeStatus.INACTIVE
    db.flush()

    s = stats.dashboard(db, company.id)

    assert (s.employees_total, s.employees_active, s.employees_linked) == (2, 1, 1)


def test_open_viaticos_match_their_balances(db: Session, company: Company, employee: Employee, second: Employee,
                                            actor: Actor) -> None:
    cali = viaticos.open_viatico(db, employee, initial_amount=D("500000"), purpose="Cali",
                                 source=MovementSource.ADMIN, actor=actor)
    viaticos.add_movement(db, cali, type=MovementType.ADDITIONAL, amount=D("100000"), note=None,
                          source=MovementSource.ADMIN, actor=actor)
    viaticos.add_movement(db, cali, type=MovementType.REFUND, amount=D("50000"), note=None,
                          source=MovementSource.ADMIN, actor=actor)
    spend(db, employee, actor, "200000", viatico=cali)
    spend(db, employee, actor, "90000", viatico=cali, status=ExpenseStatus.REJECTED)  # no cuenta
    bogota = viaticos.open_viatico(db, second, initial_amount=D("100000"), purpose="Bogotá",
                                   source=MovementSource.ADMIN, actor=actor)
    spend(db, second, actor, "130000", viatico=bogota, status=ExpenseStatus.APPROVED)  # gastó de más
    closed = viaticos.open_viatico(db, second, initial_amount=D("999999"), purpose="Viejo",
                                   source=MovementSource.ADMIN, actor=actor)
    viaticos.close_viatico(db, closed, actor)  # los cerrados no cuentan

    s = stats.dashboard(db, company.id)

    balances = [viaticos.compute_balance(db, v) for v in (cali, bogota)]
    assert s.viaticos_open == 2
    assert s.viaticos_delivered == sum(b.delivered - b.refunded for b in balances) == D("650000")
    assert s.viaticos_spent == sum(b.spent for b in balances) == D("330000")
    assert s.viaticos_remaining == sum(b.remaining for b in balances) == D("320000")
    assert s.viaticos_overspent == 1


# --- CA-3: consultas fijas ---


def count_queries(db: Session, run) -> int:
    calls = []
    listener = lambda *args: calls.append(1)  # noqa: E731
    event.listen(db.get_bind(), "before_cursor_execute", listener)
    try:
        run()
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", listener)
    return len(calls)


def test_number_of_queries_does_not_grow_with_data(db: Session, company: Company, employee: Employee,
                                                   actor: Actor) -> None:
    catalog.ensure_default_categories(db, company.id)
    spend(db, employee, actor, "1000")
    few = count_queries(db, lambda: stats.dashboard(db, company.id))

    for i in range(25):
        spend(db, employee, actor, str(1000 + i), category="Peajes")
    for i in range(4):
        v = viaticos.open_viatico(db, employee, initial_amount=D("50000"), purpose=f"V{i}",
                                  source=MovementSource.ADMIN, actor=actor)
        spend(db, employee, actor, "1000", viatico=v)
    db.flush()
    many = count_queries(db, lambda: stats.dashboard(db, company.id))

    assert many == few, f"{few} consultas con pocos datos, {many} con muchos"


# --- CA-4: empresa y permisos ---


def test_other_company_data_is_not_included(db: Session, company: Company, data, actor: Actor) -> None:
    other_company = Company(name="Otra S.A.S.")
    db.add(other_company)
    db.flush()
    outsider = Employee(company_id=other_company.id, first_name="X", last_name="Y", internal_id="EXT-1")
    db.add(outsider)
    db.flush()
    spend(db, outsider, actor, "5000000")
    viaticos.open_viatico(db, outsider, initial_amount=D("700000"), purpose="Ajeno",
                          source=MovementSource.ADMIN, actor=actor)

    mine = stats.dashboard(db, company.id)
    assert mine.month_total == D("450000")
    assert (mine.viaticos_open, mine.viaticos_delivered) == (0, D("0"))
    assert stats.dashboard(db, other_company.id).month_total == D("5000000")


def test_endpoint_shape_and_auditor_access(client, csrf, db: Session, admin: AdminUser, data) -> None:
    admin.role = AdminRole.AUDITOR
    db.flush()

    body = client.get("/api/v1/stats/dashboard").json()

    assert body["month"]["total"] == "450000.00"
    assert body["month"]["change"] == pytest.approx(21.5)  # (450.000 − 20.000) / 20.000
    assert len(body["daily"]) == 30 and body["daily"][-1]["date"] == TODAY.isoformat()
    assert body["by_status"]["in_review"] == {"count": 1, "total": "300000.00"}
    assert body["by_employee"][0]["name"] == "Ana Ruiz"
    assert set(body) >= {"employees", "viaticos", "unregistered_photos", "by_category"}


def test_change_is_null_without_previous_month(client, csrf, db: Session, company: Company, employee: Employee,
                                               actor: Actor) -> None:
    spend(db, employee, actor, "1000")
    assert client.get("/api/v1/stats/dashboard").json()["month"]["change"] is None


def test_requires_session(db: Session) -> None:
    from fastapi.testclient import TestClient

    from app.main import app
    assert TestClient(app).get("/api/v1/stats/dashboard").status_code == 401
