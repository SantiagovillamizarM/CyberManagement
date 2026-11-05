"""Paso 7.2 (backend): lo que necesita la pantalla de facturas del panel."""

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AdminUser, Company, Employee
from app.models.enums import DocumentKind
from app.services import documents, expenses
from app.services.audit import Actor
from tests.test_documents import api_storage, make_image, storage  # noqa: F401
from tests.test_expenses import BASE, act, create


def descriptions(client: TestClient, **params) -> set[str]:
    body = client.get(BASE, params=params).json()
    return {e["description"] for e in body["items"]}


def test_free_text_search(client: TestClient, csrf: dict, employee: Employee) -> None:
    create(client, csrf, employee, description="Almuerzo con cliente", invoice_number="FE-1",
           supplier_nit="800.197.268-4", supplier_name="Restaurante El Cliente")
    create(client, csrf, employee, description="Taxi al aeropuerto", invoice_number="POS-77",
           supplier_nit="900.123.456-8", supplier_name="Taxis Libres")

    assert descriptions(client, search="almuerzo") == {"Almuerzo con cliente"}  # descripción
    assert descriptions(client, search="libres") == {"Taxi al aeropuerto"}  # establecimiento
    assert descriptions(client, search="pos-77") == {"Taxi al aeropuerto"}  # n.º de factura
    assert descriptions(client, search="900.123") == {"Taxi al aeropuerto"}  # NIT
    assert descriptions(client, search="no existe") == set()


def test_search_is_parameterized(client: TestClient, csrf: dict, employee: Employee) -> None:
    create(client, csrf, employee)
    for text in ("%", "' OR 1=1 --", "_"):
        assert client.get(BASE, params={"search": text}).status_code == 200


def test_several_statuses_at_once(client: TestClient, csrf: dict, employee: Employee) -> None:
    sent = create(client, csrf, employee, description="Enviado", invoice_number="A1")
    act(client, csrf, sent["id"], "submit")
    review = create(client, csrf, employee, description="En revisión", invoice_number="A2")
    act(client, csrf, review["id"], "submit")
    act(client, csrf, review["id"], "start-review")
    create(client, csrf, employee, description="Borrador", invoice_number="A3")

    to_review = descriptions(client, status=["submitted", "in_review"])
    assert to_review == {"Enviado", "En revisión"}
    assert descriptions(client, status="draft") == {"Borrador"}  # un solo estado sigue funcionando


def test_history_shows_who_changed_what(client: TestClient, csrf: dict, db: Session, employee: Employee,
                                        admin: AdminUser) -> None:
    expense = create(client, csrf, employee)
    client.patch(f"{BASE}/{expense['id']}", headers=csrf, json={"version": expense["version"], "total": "60000"})
    act(client, csrf, expense["id"], "submit")
    act(client, csrf, expense["id"], "reject", reason="Falta el RUT del proveedor")
    model = expenses.get_expense(db, admin.company_id, expense["id"])
    expenses.update_expense(db, model, {"description": "Almuerzo corregido"}, version=model.version,
                            actor=Actor.from_employee(employee))  # el empleado lo corrige desde el bot

    history = client.get(f"{BASE}/{expense['id']}/history").json()

    total_change = next(h for h in history if h["field"] == "total")
    assert (total_change["old_value"], total_change["new_value"]) == ("59500.00", "60000.00")
    assert total_change["actor_type"] == "admin" and total_change["actor_name"] == admin.full_name
    rejection = next(h for h in history if h["action"] == "status_rejected")
    assert rejection["reason"] == "Falta el RUT del proveedor"
    fix = next(h for h in history if h["field"] == "description")
    assert fix["actor_name"] == f"{employee.first_name} {employee.last_name}"
    assert [h["created_at"] for h in history] == sorted(h["created_at"] for h in history)


def test_history_includes_the_photo(client: TestClient, csrf: dict, db: Session, employee: Employee,
                                    admin: AdminUser, api_storage) -> None:  # noqa: F811
    expense = create(client, csrf, employee)
    client.post(f"{BASE}/{expense['id']}/documents", headers=csrf,
                files={"file": ("f.jpg", make_image(color="red"), "image/jpeg")})

    history = client.get(f"{BASE}/{expense['id']}/history").json()

    assert {h["entity_type"] for h in history} == {"expense", "document"}


def test_history_of_other_company_is_404(client: TestClient, csrf: dict) -> None:
    assert client.get(f"{BASE}/00000000-0000-0000-0000-000000000000/history").status_code == 404


def test_unattached_photos(client: TestClient, csrf: dict, db: Session, company: Company, employee: Employee,
                           admin: AdminUser, storage) -> None:  # noqa: F811
    actor = Actor.from_admin(admin)
    loose = documents.store_document(db, storage, company_id=company.id, data=make_image(color="blue"),
                                     kind=DocumentKind.INVOICE_PHOTO, actor=actor, employee_id=employee.id).document
    attached = documents.store_document(db, storage, company_id=company.id, data=make_image(color="green"),
                                        kind=DocumentKind.INVOICE_PHOTO, actor=actor, employee_id=employee.id).document
    expense = expenses.get_expense(db, company.id, create(client, csrf, employee)["id"])
    documents.attach_to_expense(db, attached, expense, actor)

    body = client.get("/api/v1/documents", params={"unattached": "true", "kind": "invoice_photo"}).json()

    assert [d["id"] for d in body["items"]] == [str(loose.id)]
