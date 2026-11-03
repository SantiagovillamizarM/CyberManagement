"""Pruebas de los endpoints de empleados (panel) y del canje interno (n8n)."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CSRF_HEADER
from app.core.config import get_settings
from app.models import AdminUser, AuditLog, Company, Employee, Expense
from app.models.enums import AdminRole
from tests.conftest import ADMIN_PASSWORD

BASE = "/api/v1/employees"
REDEEM = "/api/internal/registration/redeem"
NEW = {"first_name": "Jhon", "last_name": "Pérez", "internal_id": "EMP-001"}


@pytest.fixture
def csrf(client: TestClient, admin: AdminUser) -> dict[str, str]:
    """Inicia sesión y devuelve la cabecera CSRF lista para usar."""
    response = client.post(
        "/api/v1/auth/login", json={"email": admin.email, "password": ADMIN_PASSWORD}
    )
    return {CSRF_HEADER: response.json()["csrf_token"]}


def create(client: TestClient, csrf: dict, **overrides) -> dict:
    response = client.post(BASE, json={**NEW, **overrides}, headers=csrf)
    assert response.status_code == 201, response.text
    return response.json()


# --- Permisos ---


def test_requires_login(client: TestClient) -> None:
    assert client.get(BASE).status_code == 401


def test_create_requires_csrf(client: TestClient, csrf: dict) -> None:
    assert client.post(BASE, json=NEW).status_code == 403


def test_auditor_can_read_but_not_write(client: TestClient, csrf: dict, admin: AdminUser) -> None:
    admin.role = AdminRole.AUDITOR

    assert client.get(BASE).status_code == 200
    assert client.post(BASE, json=NEW, headers=csrf).status_code == 403


# --- Crear y listar ---


def test_create_employee(client: TestClient, csrf: dict, db: Session) -> None:
    body = create(client, csrf)

    assert body["first_name"] == "Jhon"
    assert body["status"] == "active"
    assert body["telegram_linked"] is False
    assert db.scalars(select(AuditLog.action)).all() == ["create"]


def test_names_are_trimmed(client: TestClient, csrf: dict) -> None:
    assert create(client, csrf, first_name="  Jhon  ")["first_name"] == "Jhon"


@pytest.mark.parametrize(
    "overrides",
    [{"first_name": "   "}, {"internal_id": "EMP 001"}, {"internal_id": "x" * 51}, {"last_name": ""}],
)
def test_invalid_data_is_rejected(client: TestClient, csrf: dict, overrides: dict) -> None:
    assert client.post(BASE, json={**NEW, **overrides}, headers=csrf).status_code == 422


def test_duplicate_internal_id_is_rejected(client: TestClient, csrf: dict) -> None:
    create(client, csrf)

    response = client.post(BASE, json={**NEW, "first_name": "Otro"}, headers=csrf)

    assert response.status_code == 409


def test_list_and_search(client: TestClient, csrf: dict) -> None:
    create(client, csrf)
    create(client, csrf, first_name="Ana", last_name="Gómez", internal_id="EMP-002")

    everyone = client.get(BASE).json()
    found = client.get(BASE, params={"search": "góm"}).json()

    assert everyone["total"] == 2
    assert [e["first_name"] for e in found["items"]] == ["Ana"]


def test_other_company_employee_is_invisible(client: TestClient, csrf: dict, db: Session) -> None:
    other_company = Company(name="Otra Empresa")
    db.add(other_company)
    db.flush()
    stranger = Employee(company_id=other_company.id, first_name="X", last_name="Y", internal_id="Z")
    db.add(stranger)
    db.flush()

    assert client.get(f"{BASE}/{stranger.id}").status_code == 404
    assert client.get(BASE).json()["total"] == 0


def test_unknown_employee_is_404(client: TestClient, csrf: dict) -> None:
    assert client.get(f"{BASE}/{uuid.uuid4()}").status_code == 404


# --- Editar ---


def test_update_records_history_per_field(client: TestClient, csrf: dict, db: Session) -> None:
    employee_id = create(client, csrf)["id"]

    response = client.patch(
        f"{BASE}/{employee_id}", json={"first_name": "John", "last_name": "Pérez"}, headers=csrf
    )

    assert response.status_code == 200
    updates = db.scalars(select(AuditLog).where(AuditLog.action == "update")).all()
    # last_name no cambió: no se registra.
    assert [(u.field, u.old_value, u.new_value) for u in updates] == [("first_name", "Jhon", "John")]
    assert updates[0].actor_type == "admin"


def test_deactivate_employee(client: TestClient, csrf: dict) -> None:
    employee_id = create(client, csrf)["id"]
    client.post(f"{BASE}/{employee_id}/registration-code", headers=csrf)

    body = client.patch(f"{BASE}/{employee_id}", json={"status": "inactive"}, headers=csrf).json()

    assert body["status"] == "inactive"
    assert body["pending_code"] is None


# --- Eliminar ---


def test_delete_employee_without_records(client: TestClient, csrf: dict) -> None:
    employee_id = create(client, csrf)["id"]

    assert client.delete(f"{BASE}/{employee_id}", headers=csrf).status_code == 204
    assert client.get(f"{BASE}/{employee_id}").status_code == 404


def test_delete_employee_that_had_codes_and_telegram(client: TestClient, csrf: dict) -> None:
    """Caso real que falló en la prueba manual: el empleado ya tuvo un código usado."""
    employee_id = create(client, csrf)["id"]
    code = client.post(f"{BASE}/{employee_id}/registration-code", headers=csrf).json()["code"]
    client.post(REDEEM, json=redeem_body(code), headers=internal_headers())
    client.post(f"{BASE}/{employee_id}/unlink-telegram", headers=csrf)
    client.post(f"{BASE}/{employee_id}/registration-code", headers=csrf)  # y uno pendiente

    assert client.delete(f"{BASE}/{employee_id}", headers=csrf).status_code == 204
    assert client.get(f"{BASE}/{employee_id}").status_code == 404


def test_cannot_delete_employee_with_expenses(
    client: TestClient, csrf: dict, db: Session, company: Company
) -> None:
    employee_id = create(client, csrf)["id"]
    db.add(Expense(company_id=company.id, employee_id=uuid.UUID(employee_id)))
    db.flush()

    response = client.delete(f"{BASE}/{employee_id}", headers=csrf)

    assert response.status_code == 409
    assert "desactívalo" in response.json()["detail"]


# --- Códigos y Telegram ---


def test_code_is_shown_only_once(client: TestClient, csrf: dict) -> None:
    employee_id = create(client, csrf)["id"]

    issued = client.post(f"{BASE}/{employee_id}/registration-code", headers=csrf).json()
    detail = client.get(f"{BASE}/{employee_id}").json()

    assert issued["code"].startswith("JHON-")
    assert detail["pending_code"]["hint"] == issued["hint"]
    assert issued["code"] not in str(detail)


def test_revoke_code(client: TestClient, csrf: dict) -> None:
    employee_id = create(client, csrf)["id"]
    client.post(f"{BASE}/{employee_id}/registration-code", headers=csrf)

    assert client.delete(f"{BASE}/{employee_id}/registration-code", headers=csrf).status_code == 204
    assert client.delete(f"{BASE}/{employee_id}/registration-code", headers=csrf).status_code == 404


# --- Endpoint interno (n8n) ---


def internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": get_settings().internal_api_token.get_secret_value()}


def redeem_body(code: str) -> dict:
    return {"code": code, "telegram_user_id": 777, "telegram_chat_id": 777, "telegram_username": "jhon"}


@pytest.mark.parametrize("headers", [{}, {"X-Internal-Token": "incorrecto"}])
def test_internal_endpoint_requires_token(client: TestClient, headers: dict) -> None:
    assert client.post(REDEEM, json=redeem_body("X"), headers=headers).status_code == 401


def test_full_flow_create_code_redeem_unlink(client: TestClient, csrf: dict) -> None:
    employee_id = create(client, csrf)["id"]
    code = client.post(f"{BASE}/{employee_id}/registration-code", headers=csrf).json()["code"]

    redeemed = client.post(REDEEM, json=redeem_body(code), headers=internal_headers())

    assert redeemed.json() == {"status": "linked", "employee_first_name": "Jhon"}
    assert client.get(f"{BASE}/{employee_id}").json()["telegram_linked"] is True

    unlinked = client.post(f"{BASE}/{employee_id}/unlink-telegram", headers=csrf)

    assert unlinked.json()["telegram_linked"] is False


def test_internal_redeem_reports_invalid_code(client: TestClient) -> None:
    response = client.post(REDEEM, json=redeem_body("NADA-0000-0000"), headers=internal_headers())

    assert response.status_code == 200
    assert response.json() == {"status": "invalid_code", "employee_first_name": None}
