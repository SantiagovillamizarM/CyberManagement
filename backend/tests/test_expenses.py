"""CA-6 a CA-15: gastos (servicio y API)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminUser, AuditLog, Category, Employee, Expense
from app.models.enums import AdminRole, DataSource, ExpenseStatus
from app.services import expenses
from app.services.audit import Actor
from app.utils.dates import local_today

BASE = "/api/v1/expenses"
D = Decimal


def payload(employee: Employee, **overrides) -> dict:
    return {
        "employee_id": str(employee.id),
        "description": "Almuerzo con cliente",
        "issue_date": "2026-11-01",
        "total": "59500.00",
        "subtotal": "50000.00",
        "tax_total": "9500.00",
        "supplier_nit": "800.197.268-4",
        "supplier_name": "Restaurante El Cliente",
        "invoice_number": "FE-1001",
        "payment_source": "own_money",
        **overrides,
    }


def create(client: TestClient, csrf: dict, employee: Employee, **overrides) -> dict:
    response = client.post(BASE, json=payload(employee, **overrides), headers=csrf)
    assert response.status_code == 201, response.text
    return response.json()


def act(client: TestClient, csrf: dict, expense_id: str, action: str, **json):
    return client.post(f"{BASE}/{expense_id}/{action}", headers=csrf, json=json or None)


# --- Registro ---


def test_create_expense(client: TestClient, csrf: dict, employee: Employee) -> None:
    body = create(client, csrf, employee)

    assert body["status"] == "draft"
    assert body["supplier"] == {"nit": "800197268", "dv": "4", "name": "Restaurante El Cliente"}
    assert body["total"] == "59500.00"  # dinero como texto: sin errores de redondeo
    assert body["location_source"] == "none"  # sin ubicación no se inventa nada
    assert body["warnings"] == []


def test_ca6_own_money_is_reimbursable(client: TestClient, csrf: dict, employee: Employee) -> None:
    assert create(client, csrf, employee)["is_reimbursable"] is True
    assert create(client, csrf, employee, invoice_number="FE-2", payment_source="corporate_card")[
        "is_reimbursable"
    ] is False


def test_wrong_nit_dv_is_rejected(client: TestClient, csrf: dict, employee: Employee) -> None:
    response = client.post(BASE, json=payload(employee, supplier_nit="800197268-9"), headers=csrf)

    assert response.status_code == 422
    assert "dígito de verificación" in response.json()["detail"]


def test_ca7_future_issue_date_is_rejected(client: TestClient, csrf: dict, employee: Employee) -> None:
    tomorrow = (local_today() + timedelta(days=1)).isoformat()

    response = client.post(BASE, json=payload(employee, issue_date=tomorrow), headers=csrf)

    assert response.status_code == 422


@pytest.mark.parametrize("field", ["total", "description", "issue_date"])
def test_ca7_submit_requires_mandatory_fields(client, csrf, employee, field) -> None:
    expense_id = create(client, csrf, employee, **{field: None})["id"]

    response = act(client, csrf, expense_id, "submit")

    assert response.status_code == 422
    assert field in response.json()["detail"]


def test_items_compute_line_totals(client: TestClient, csrf: dict, employee: Employee) -> None:
    body = create(
        client, csrf, employee,
        subtotal="50000.00",
        items=[
            {"description": "Bandeja paisa", "quantity": "2", "unit_price": "20000"},
            {"description": "Jugo", "quantity": "2", "unit_price": "5000"},
        ],
    )

    assert [i["line_total"] for i in body["items"]] == ["40000.00", "10000.00"]
    assert body["warnings"] == []


def test_ca13_inconsistencies_warn_but_do_not_block(client, csrf, employee) -> None:
    body = create(
        client, csrf, employee,
        total="70000.00",
        supplier_nit=None,
        items=[{"description": "Producto", "unit_price": "1000"}],
    )

    assert set(body["warnings"]) == {
        "El subtotal más los impuestos no coincide con el total",
        "La suma de los productos no coincide con el subtotal ni con el total",
        "No se registró el NIT del establecimiento",
    }


def test_items_with_tax_included_prices_do_not_warn(client, csrf, employee) -> None:
    """En Colombia es normal que los productos ya traigan el IVA: suman el total, no el subtotal."""
    body = create(
        client, csrf, employee,
        total="68000.00", subtotal="62963.00", tax_total="5037.00",
        items=[{"description": "Bandeja paisa", "quantity": "2", "unit_price": "28000"},
               {"description": "Limonada", "quantity": "2", "unit_price": "6000"}],
    )

    assert body["warnings"] == []


def test_manual_location_is_recorded(client: TestClient, csrf: dict, employee: Employee) -> None:
    body = create(client, csrf, employee, location_lat="6.2442031", location_lon="-75.5812119",
                  location_text="Medellín")

    assert body["location_source"] == "manual"
    assert body["location_lat"] == "6.244203"


def test_location_needs_both_coordinates(client: TestClient, csrf: dict, employee: Employee) -> None:
    response = client.post(BASE, json=payload(employee, location_lat="6.24"), headers=csrf)

    assert response.status_code == 422


# --- Duplicados ---


def test_ca12_duplicate_invoice_number_for_same_nit(client, csrf, employee) -> None:
    first = create(client, csrf, employee)

    response = client.post(BASE, json=payload(employee), headers=csrf)

    assert response.status_code == 409
    assert response.json()["detail"]["existing_expense_id"] == first["id"]


def test_ca12_duplicate_cufe(client, csrf, employee) -> None:
    create(client, csrf, employee, cufe="CUFE-ABC")

    response = client.post(BASE, json=payload(employee, cufe="CUFE-ABC", invoice_number="FE-9"),
                           headers=csrf)

    assert response.status_code == 409


def test_ca12_editing_into_a_duplicate_is_409_not_500(client, csrf, employee) -> None:
    """Regresión (paso 4.4): al EDITAR, el autoflush enviaba el cambio a la BD antes de
    validarlo y la restricción única respondía con un error 500."""
    first = create(client, csrf, employee)
    second = create(client, csrf, employee, invoice_number="FE-2")

    response = client.patch(f"{BASE}/{second['id']}", headers=csrf,
                            json={"version": second["version"], "invoice_number": first["invoice_number"]})

    assert response.status_code == 409
    assert response.json()["detail"]["existing_expense_id"] == first["id"]
    assert client.get(f"{BASE}/{second['id']}").json()["invoice_number"] == "FE-2"


def test_same_invoice_number_from_other_supplier_is_fine(client, csrf, employee) -> None:
    create(client, csrf, employee)

    create(client, csrf, employee, supplier_nit="890903938-8")


# --- Ciclo de aprobación ---


def test_full_approval_cycle(client: TestClient, csrf: dict, employee: Employee) -> None:
    expense_id = create(client, csrf, employee)["id"]

    assert act(client, csrf, expense_id, "submit").json()["status"] == "submitted"
    assert act(client, csrf, expense_id, "start-review").json()["status"] == "in_review"
    approved = act(client, csrf, expense_id, "approve").json()
    assert approved["status"] == "approved"
    assert approved["reviewed_at"] is not None
    assert act(client, csrf, expense_id, "close").json()["status"] == "closed"


def test_ca8_invalid_transitions(client: TestClient, csrf: dict, employee: Employee) -> None:
    expense_id = create(client, csrf, employee)["id"]

    assert act(client, csrf, expense_id, "approve").status_code == 409  # aprobar un borrador
    assert act(client, csrf, expense_id, "close").status_code == 409


def test_ca8_reject_requires_reason_and_can_be_resubmitted(client, csrf, employee) -> None:
    expense_id = create(client, csrf, employee)["id"]
    act(client, csrf, expense_id, "submit")

    assert act(client, csrf, expense_id, "reject", reason="").status_code == 422
    rejected = act(client, csrf, expense_id, "reject", reason="Falta la factura original").json()
    assert rejected["status"] == "rejected"
    assert rejected["rejection_reason"] == "Falta la factura original"

    resubmitted = act(client, csrf, expense_id, "submit").json()
    assert resubmitted["status"] == "submitted"
    assert resubmitted["rejection_reason"] is None


def test_cannot_delete_approved_expense(client, csrf, employee) -> None:
    expense_id = create(client, csrf, employee)["id"]
    act(client, csrf, expense_id, "submit")
    act(client, csrf, expense_id, "approve")

    assert client.delete(f"{BASE}/{expense_id}", headers=csrf).status_code == 409


def test_deleted_expense_disappears_but_is_kept(client, csrf, employee, db: Session) -> None:
    expense_id = create(client, csrf, employee)["id"]

    assert client.delete(f"{BASE}/{expense_id}", headers=csrf).status_code == 204
    assert client.get(f"{BASE}/{expense_id}").status_code == 404
    assert db.scalars(select(Expense)).one().deleted_at is not None  # sigue en la base


# --- Edición, historial y concurrencia ---


def test_ca10_edit_records_history_including_nit(client, csrf, employee, db: Session) -> None:
    created = create(client, csrf, employee)

    response = client.patch(
        f"{BASE}/{created['id']}",
        json={"version": created["version"], "total": "60000.00", "supplier_nit": "890903938-8"},
        headers=csrf,
    )

    assert response.status_code == 200
    rows = db.scalars(select(AuditLog).where(AuditLog.action == "update")).all()
    history = {r.field: (r.old_value, r.new_value) for r in rows}
    assert history == {
        "total": ("59500.00", "60000.00"),
        "supplier_nit": ("800197268", "890903938"),
    }


def test_ca10_items_change_is_recorded(client, csrf, employee, db: Session) -> None:
    created = create(client, csrf, employee, items=[{"description": "A", "line_total": "100"}])

    client.patch(f"{BASE}/{created['id']}", headers=csrf,
                 json={"version": created["version"], "items": [{"description": "B", "line_total": "200"}]})

    row = db.scalars(select(AuditLog).where(AuditLog.field == "items")).one()
    assert row.old_value[0]["description"] == "A"
    assert row.new_value[0]["description"] == "B"


def test_null_clears_a_field(client, csrf, employee) -> None:
    created = create(client, csrf, employee)

    body = client.patch(f"{BASE}/{created['id']}", headers=csrf,
                        json={"version": created["version"], "invoice_number": None}).json()

    assert body["invoice_number"] is None
    assert body["supplier"]["nit"] == "800197268"  # lo no enviado no cambia


def test_ca11_stale_version_is_rejected(client, csrf, employee) -> None:
    created = create(client, csrf, employee)
    url = f"{BASE}/{created['id']}"
    client.patch(url, json={"version": created["version"], "total": "1.00"}, headers=csrf)

    # Otra persona edita con la versión vieja que tenía en pantalla.
    response = client.patch(url, json={"version": created["version"], "total": "2.00"}, headers=csrf)

    assert response.status_code == 409
    assert client.get(url).json()["total"] == "1.00"


def test_items_only_change_bumps_version(client, csrf, employee) -> None:
    created = create(client, csrf, employee)

    body = client.patch(f"{BASE}/{created['id']}", headers=csrf,
                        json={"version": created["version"], "items": [{"description": "X"}]}).json()

    assert body["version"] == created["version"] + 1


def test_correcting_ai_data_marks_it_mixed(db: Session, employee: Employee, admin: AdminUser) -> None:
    actor = Actor.from_admin(admin)
    expense = expenses.create_expense(db, employee, {"total": D("100")}, actor=actor,
                                      data_source=DataSource.AI)

    expenses.update_expense(db, expense, {"total": D("110")}, version=expense.version, actor=actor)

    assert expense.data_source == DataSource.MIXED


# --- CA-9: permisos del empleado frente al administrador ---


def _approved(db: Session, employee: Employee, admin: AdminUser) -> Expense:
    actor = Actor.from_admin(admin)
    expense = expenses.create_expense(
        db, employee, {"total": D("100"), "description": "x", "issue_date": date(2026, 11, 1)}, actor=actor
    )
    expenses.submit(db, expense, actor)
    expenses.approve(db, expense, actor)
    return expense


def test_ca9_employee_cannot_edit_approved(db: Session, employee: Employee, admin: AdminUser) -> None:
    expense = _approved(db, employee, admin)

    with pytest.raises(expenses.ExpenseLockedError):
        expenses.update_expense(db, expense, {"total": D("1")}, version=expense.version,
                                actor=Actor.from_employee(employee))


def test_ca9_employee_can_edit_while_submitted(db: Session, employee: Employee, admin: AdminUser) -> None:
    actor = Actor.from_employee(employee)
    expense = expenses.create_expense(
        db, employee, {"total": D("100"), "description": "x", "issue_date": date(2026, 11, 1)}, actor=actor
    )
    expenses.submit(db, expense, actor)

    expenses.update_expense(db, expense, {"total": D("120")}, version=expense.version, actor=actor)

    assert expense.total == D("120")


def test_ca9_employee_cannot_edit_someone_else(db, company, employee: Employee, admin) -> None:
    expense = expenses.create_expense(db, employee, {"total": D("1")}, actor=Actor.from_admin(admin))
    other = Employee(company_id=company.id, first_name="Ana", last_name="G", internal_id="E2")
    db.add(other)
    db.flush()

    with pytest.raises(expenses.ExpenseLockedError):
        expenses.update_expense(db, expense, {"total": D("2")}, version=expense.version,
                                actor=Actor.from_employee(other))


def test_ca9_admin_can_edit_approved_and_it_is_audited(client, csrf, employee, db, admin) -> None:
    expense = _approved(db, employee, admin)

    response = client.patch(f"{BASE}/{expense.id}", headers=csrf,
                            json={"version": expense.version, "description": "Corregido"})

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert db.scalars(select(AuditLog).where(AuditLog.field == "description")).one().new_value == "Corregido"


# --- CA-14: filtros ---


def test_ca14_filters(client: TestClient, csrf: dict, employee: Employee, company, db: Session) -> None:
    ana = Employee(company_id=company.id, first_name="Ana", last_name="G", internal_id="E2")
    db.add(ana)
    db.flush()
    a = create(client, csrf, employee, issue_date="2026-10-05", total="10000", invoice_number="A-1",
               subtotal=None, tax_total=None)
    b = create(client, csrf, ana, issue_date="2026-11-01", total="90000", invoice_number="B-7",
               supplier_nit="890903938-8", supplier_name="Bancolombia", subtotal=None, tax_total=None)
    act(client, csrf, b["id"], "submit")

    def ids(**params) -> set[str]:
        return {e["id"] for e in client.get(BASE, params=params).json()["items"]}

    assert ids(employee_id=str(ana.id)) == {b["id"]}
    assert ids(issue_date_from="2026-11-01") == {b["id"]}
    assert ids(issue_date_to="2026-10-31") == {a["id"]}
    assert ids(establishment="banco") == {b["id"]}
    assert ids(supplier_nit="890.903.938-8") == {b["id"]}
    assert ids(min_total="50000") == {b["id"]}
    assert ids(max_total="50000") == {a["id"]}
    assert ids(status="submitted") == {b["id"]}
    assert ids(invoice_number="a-1") == {a["id"]}
    assert client.get(BASE).json()["total"] == 2


def test_category_filter_and_validation(client, csrf, employee, company, db: Session) -> None:
    category = Category(company_id=company.id, name="Transporte")
    db.add(category)
    db.flush()
    with_category = create(client, csrf, employee, category_id=str(category.id))
    create(client, csrf, employee, invoice_number="FE-2")

    found = client.get(BASE, params={"category_id": str(category.id)}).json()["items"]

    assert [e["id"] for e in found] == [with_category["id"]]
    assert found[0]["category"]["name"] == "Transporte"


# --- CA-15: roles ---


def test_ca15_auditor_reads_but_cannot_write(client, csrf, employee, admin: AdminUser) -> None:
    expense_id = create(client, csrf, employee)["id"]
    admin.role = AdminRole.AUDITOR

    assert client.get(BASE).status_code == 200
    assert client.get(f"{BASE}/{expense_id}").status_code == 200
    assert client.post(BASE, json=payload(employee, invoice_number="X"), headers=csrf).status_code == 403
    assert act(client, csrf, expense_id, "submit").status_code == 403


def test_requires_login(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401
