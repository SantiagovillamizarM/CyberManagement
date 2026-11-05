"""Paso 7.3 (backend): lo que necesita la pantalla de viáticos del panel."""

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AdminUser, Employee, Viatico
from app.models.enums import ExpenseStatus, MovementSource
from app.services import expenses, viaticos
from app.services.audit import Actor
from tests.test_viaticos import spend

BASE = "/api/v1/viaticos"


def open_one(client: TestClient, csrf: dict, employee: Employee, amount="500000") -> dict:
    response = client.post(BASE, headers=csrf, json={"employee_id": str(employee.id), "initial_amount": amount,
                                                     "purpose": "Viaje a Cali"})
    assert response.status_code == 201, response.text
    return response.json()


def test_movements_show_who_registered_them(client: TestClient, csrf: dict, employee: Employee,
                                            admin: AdminUser) -> None:
    viatico = open_one(client, csrf, employee)

    body = client.post(f"{BASE}/{viatico['id']}/movements", headers=csrf,
                       json={"type": "additional", "amount": "100000", "note": "Segunda semana"}).json()

    assert [m["created_by_name"] for m in body["movements"]] == [admin.full_name, admin.full_name]
    assert body["balance"]["delivered"] == "600000.00"


def test_unresolved_expenses_are_counted_and_block_closing(client: TestClient, csrf: dict, db: Session,
                                                           employee: Employee, admin: AdminUser) -> None:
    actor = Actor.from_admin(admin)
    viatico_out = open_one(client, csrf, employee)
    viatico = db.get(Viatico, viatico_out["id"])
    pending = spend(db, employee, viatico, actor, "40000", status=ExpenseStatus.SUBMITTED)
    spend(db, employee, viatico, actor, "10000", status=ExpenseStatus.APPROVED)

    assert client.get(f"{BASE}/{viatico.id}").json()["unresolved_expenses"] == 1
    response = client.post(f"{BASE}/{viatico.id}/close", headers=csrf)
    assert response.status_code == 409 and "1 gasto" in response.json()["detail"]

    expenses.reject(db, pending, actor, "No corresponde al viaje")
    body = client.post(f"{BASE}/{viatico.id}/close", headers=csrf).json()
    assert body["status"] == "closed" and body["unresolved_expenses"] == 0
    assert body["balance"]["remaining"] == "490000.00"  # el rechazado no cuenta como gastado
    assert body["balance"]["settlement"] == "employee_must_return"


def test_viatico_declared_by_employee_has_no_admin_author(client: TestClient, db: Session, employee: Employee,
                                                          csrf: dict) -> None:
    viatico = viaticos.open_viatico(db, employee, initial_amount=Decimal("80000"), purpose="Caja menor",
                                    source=MovementSource.EMPLOYEE, actor=Actor.from_employee(employee))

    (movement,) = client.get(f"{BASE}/{viatico.id}").json()["movements"]
    assert movement["source"] == "employee" and movement["created_by_name"] is None
