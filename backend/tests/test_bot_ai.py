"""Paso 5.2: el bot usa la lectura de la IA (escenarios A, B y C del requerimiento 3.1).

La IA es un modelo FALSO (tests/test_ai.FakeModel): no se llama a Gemini ni se gasta cuota.
"""

import json
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.gemini import GeminiError, GeminiErrorKind, get_vision_model
from app.bot import engine, expense_flow as flow
from app.bot import messages as msg
from app.main import app
from app.models import BotSession, Company, Document, Employee, Expense, Supplier
from app.models.enums import DataSource, DocumentKind, ExpenseStatus, ProcessingStatus
from app.storage import get_storage
from app.utils.dates import local_today
from tests.test_ai import NIT, FakeModel, reply
from tests.test_bot import USER, buttons, texts
from tests.test_bot_expense import category_button, linked, say, state  # noqa: F401
from tests.test_bot_files import photo_update, to_photo_step
from tests.test_documents import make_image, storage  # noqa: F401

NOT_INVOICE = json.dumps({"is_invoice": False, "legible": True, "confidence": 0.97, "items": [],
                          "not_invoice_reason": "Es una selfie"})
_colors = iter(range(10, 250, 7))


def new_photo() -> bytes:
    """Cada foto distinta (si no, se detectaría como la misma imagen ya guardada)."""
    return make_image(color=(next(_colors), 120, 90))


def deliver(db: Session, storage, model: FakeModel, data: bytes | None = None) -> list[dict]:
    engine.handle_update(db, photo_update())  # el empleado envía la foto → n8n la descarga
    return engine.handle_file(db, storage, telegram_user_id=USER, purpose=flow.INVOICE, data=data or new_photo(),
                              file_name="factura.jpg", file_unique_id=None, vision=model)


def data(db: Session) -> dict:
    return db.get(BotSession, USER).data


def only_expense(db: Session) -> Expense:
    return db.scalars(select(Expense)).one()


# --- CA-1 y CA-2: escenario A ---


def test_ca1_scenario_a_fills_everything_and_asks_only_what_is_missing(db: Session, linked: Employee,
                                                                       storage) -> None:
    to_photo_step(db)

    actions = deliver(db, storage, FakeModel(reply()))

    # Proveedor nuevo: lo único que pregunta antes del resumen es el RUT (opcional) y la ubicación.
    assert texts(actions) == [msg.EXP_AI_READ, msg.EXP_ASK_RUT]
    actions = say(db, ("btn", flow.CB_SKIP))
    assert texts(actions) == [msg.EXP_ASK_LOCATION]
    summary = texts(say(db, ("btn", flow.CB_SKIP)))[-1]

    assert summary.startswith(msg.EXP_SUMMARY_TITLE_AI)
    assert msg.EXP_SUMMARY_QUESTION_AI in summary
    for expected in ("$45.378,14", "Almuerzo en restaurante", "Restaurante El Buen Sabor", NIT, "FE-1234",
                     "Alimentación", "2 × Bandeja paisa", "$42.016,80", "$3.361,34"):
        assert expected in summary, expected
    assert state(db) == flow.CONFIRM


def test_ca2_confirmed_ai_expense_keeps_everything_read(db: Session, linked: Employee, storage) -> None:
    to_photo_step(db)
    deliver(db, storage, FakeModel(reply(cufe="ab" * 48)))
    say(db, ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP))

    assert msg.EXP_SAVED in texts(say(db, ("btn", flow.CB_CONFIRM)))[0]

    expense = only_expense(db)
    assert expense.status == ExpenseStatus.SUBMITTED
    assert expense.data_source == DataSource.AI
    assert expense.total == Decimal("45378.14")
    assert (expense.subtotal, expense.tax_total) == (Decimal("42016.80"), Decimal("3361.34"))
    assert expense.issue_date == local_today() and expense.issue_time.strftime("%H:%M") == "13:45"
    assert expense.invoice_number == "FE-1234" and expense.cufe == "ab" * 48
    assert [(i.description, i.quantity, i.unit_price) for i in expense.items] == [
        ("Bandeja paisa", Decimal("2.000"), Decimal("21008.40"))]
    supplier = db.get(Supplier, expense.supplier_id)
    assert (supplier.nit, supplier.name) == ("900123456", "Restaurante El Buen Sabor S.A.S.")
    document = db.scalars(select(Document)).one()
    assert document.expense_id == expense.id
    assert document.processing_status == ProcessingStatus.EXTRACTED


def test_ca1_known_supplier_skips_name_and_rut(db: Session, linked: Employee, company: Company,
                                               storage) -> None:
    db.add(Supplier(company_id=company.id, nit="900123456", dv=NIT[-1], name="El Buen Sabor (registrado)"))
    to_photo_step(db)

    assert texts(deliver(db, storage, FakeModel(reply()))) == [msg.EXP_AI_READ, msg.EXP_ASK_LOCATION]
    assert "El Buen Sabor (registrado)" in texts(say(db, ("btn", flow.CB_SKIP)))[-1]  # el nombre registrado manda


def test_ca1_missing_fields_are_asked_and_marked_mixed(db: Session, linked: Employee, company: Company,
                                                       storage) -> None:
    to_photo_step(db)

    actions = deliver(db, storage, FakeModel(reply(issue_date=None, category=None, supplier_nit=None)))

    assert texts(actions) == [msg.EXP_AI_READ, msg.EXP_ASK_DATE]  # sin NIT: no hay proveedor que nombrar
    assert texts(say(db, ("btn", flow.CB_TODAY))) == [msg.EXP_ASK_CATEGORY]
    say(db, category_button(db, company, "Transporte"), ("btn", flow.CB_SKIP), ("btn", flow.CB_CONFIRM))

    expense = only_expense(db)
    assert expense.data_source == DataSource.MIXED  # el empleado escribió la fecha
    assert expense.supplier_id is None


def test_ca6_correcting_total_drops_items_that_no_longer_match(db: Session, linked: Employee, storage) -> None:
    to_photo_step(db)
    deliver(db, storage, FakeModel(reply()))
    say(db, ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP))

    summary = texts(say(db, ("btn", flow.CB_EDIT), ("btn", f"{flow.CB_FIELD}total"), "50.000"))[-1]
    assert "Bandeja paisa" not in summary and "$50.000" in summary
    say(db, ("btn", flow.CB_CONFIRM))

    expense = only_expense(db)
    assert expense.total == Decimal("50000.00") and expense.items == []
    assert expense.data_source == DataSource.MIXED


def test_ca6_new_photo_from_edit_is_read_again(db: Session, linked: Employee, storage) -> None:
    to_photo_step(db)
    deliver(db, storage, FakeModel(reply()))
    say(db, ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP), ("btn", flow.CB_EDIT), ("btn", f"{flow.CB_FIELD}photo"))

    actions = deliver(db, storage, FakeModel(reply(total=99000, subtotal=None, tax_total=None, items=[])))

    assert texts(actions)[0] == msg.EXP_AI_READ
    assert "$99.000" in texts(actions)[-1]  # vuelve directo al resumen con los datos nuevos
    assert state(db) == flow.CONFIRM


def test_photo_sent_from_menu_still_asks_money_then_skips(db: Session, linked: Employee, storage) -> None:
    engine.handle_update(db, photo_update())  # foto directamente desde el menú
    actions = engine.handle_file(db, storage, telegram_user_id=USER, purpose=flow.INVOICE, data=new_photo(),
                                 file_name=None, file_unique_id=None, vision=FakeModel(reply()))

    assert texts(actions) == [msg.EXP_AI_READ, msg.EXP_HAD_MONEY]
    assert texts(say(db, ("btn", flow.CB_NO))) == [msg.EXP_ASK_RUT]  # no vuelve a pedir la foto ni los datos


# --- CA-3: escenario B ---


def test_ca3_scenario_b_keeps_photo_pending_and_asks_manually(db: Session, linked: Employee, company: Company,
                                                              storage) -> None:
    to_photo_step(db)

    actions = deliver(db, storage, FakeModel(reply(total=None, legible=False)))

    assert texts(actions) == [msg.EXP_AI_UNREADABLE, msg.EXP_ASK_DESCRIPTION]
    document = db.scalars(select(Document)).one()
    assert document.processing_status == ProcessingStatus.NEEDS_MANUAL_INPUT  # pendiente de completar
    assert data(db)["document_id"] == str(document.id)

    summary = texts(say(db, "Almuerzo", "45.000", ("btn", flow.CB_SKIP), ("btn", flow.CB_TODAY),
                        ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP), category_button(db, company),
                        ("btn", flow.CB_SKIP)))[-1]
    assert summary.startswith(msg.EXP_SUMMARY_TITLE)
    say(db, ("btn", flow.CB_CONFIRM))
    assert only_expense(db).data_source == DataSource.MANUAL
    assert document.expense_id == only_expense(db).id  # la foto quedó en el gasto


# --- CA-4: escenario C ---


def test_ca4_scenario_c_not_an_invoice_asks_for_another_photo(db: Session, linked: Employee, storage) -> None:
    to_photo_step(db)

    actions = deliver(db, storage, FakeModel(NOT_INVOICE))

    assert texts(actions) == [msg.EXP_AI_NOT_INVOICE, msg.EXP_ASK_PHOTO]
    assert flow.CB_NO_PHOTO in buttons(actions)
    assert state(db) == flow.PHOTO
    assert data(db).get("document_id") is None  # no se asocia al gasto
    rejected = db.scalars(select(Document)).one()
    assert rejected.processing_status == ProcessingStatus.NOT_INVOICE  # pero sí queda el registro técnico
    assert rejected.extractions[0].parsed["not_invoice_reason"] == "Es una selfie"

    # Envía otra foto, que sí es una factura.
    assert texts(deliver(db, storage, FakeModel(reply())))[0] == msg.EXP_AI_READ


def test_ca4_not_invoice_never_becomes_an_expense(db: Session, linked: Employee, company: Company,
                                                  storage) -> None:
    to_photo_step(db)
    deliver(db, storage, FakeModel(NOT_INVOICE))
    say(db, ("btn", flow.CB_NO_PHOTO), "Taxi", "12.000", ("btn", flow.CB_SKIP), ("btn", flow.CB_TODAY),
        ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP), category_button(db, company), ("btn", flow.CB_SKIP),
        ("btn", flow.CB_CONFIRM))

    rejected = db.scalars(select(Document)).one()
    assert rejected.expense_id is None
    assert only_expense(db).total == Decimal("12000.00")


# --- CA-5: Gemini falla ---


@pytest.mark.parametrize("kind", [GeminiErrorKind.RATE_LIMITED, GeminiErrorKind.TIMEOUT, GeminiErrorKind.NETWORK])
def test_ca5_gemini_failure_keeps_photo_and_continues_manually(db: Session, linked: Employee, storage,
                                                               kind) -> None:
    to_photo_step(db)

    actions = deliver(db, storage, FakeModel(error=GeminiError(kind, "falla")))

    assert texts(actions) == [msg.EXP_AI_FAILED, msg.EXP_ASK_DESCRIPTION]
    document = db.scalars(select(Document)).one()
    assert document.processing_status == ProcessingStatus.FAILED  # se puede reintentar desde el panel
    assert data(db)["document_id"] == str(document.id)


def test_ca5_rut_is_never_sent_to_the_ai(db: Session, linked: Employee, storage) -> None:
    to_photo_step(db)
    deliver(db, storage, FakeModel(reply()))  # proveedor nuevo → pide el RUT
    model = FakeModel(reply())

    actions = engine.handle_file(db, storage, telegram_user_id=USER, purpose=flow.RUT_FILE, data=new_photo(),
                                 file_name="rut.jpg", file_unique_id=None, vision=model)

    assert texts(actions)[0] == msg.EXP_RUT_SAVED
    assert model.calls == []
    rut = db.scalars(select(Document).where(Document.kind == DocumentKind.RUT)).one()
    assert rut.extractions == []


# --- Endpoint real que llama n8n ---


def test_files_endpoint_uses_the_ai(client, db: Session, linked: Employee, storage) -> None:
    model = FakeModel(reply())
    app.dependency_overrides[get_vision_model] = lambda: model
    app.dependency_overrides[get_storage] = lambda: storage
    to_photo_step(db)

    response = client.post("/api/internal/telegram/files",
                           files={"file": ("f.jpg", new_photo(), "image/jpeg")},
                           data={"telegram_user_id": str(USER), "purpose": "invoice"},
                           headers={"X-Internal-Token": engine.get_settings().internal_api_token.get_secret_value()})

    assert response.status_code == 200
    assert response.json()["actions"][0]["payload"]["text"] == msg.EXP_AI_READ
    assert len(model.calls) == 1
