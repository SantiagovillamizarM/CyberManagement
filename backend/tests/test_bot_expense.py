"""Paso 4.2: registro de gastos por conversación (CA-1 a CA-11)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import engine, expense_flow as flow
from app.bot import messages as msg
from app.models import AdminUser, AuditLog, BotSession, Company, Employee, Expense, Supplier, Viatico
from app.models.enums import ExpenseStatus, LocationSource, MovementSource, PaymentSource
from app.services import catalog, expenses, viaticos
from app.services.audit import Actor
from app.utils.dates import InvalidDateError, local_today, parse_invoice_date
from app.utils.money import InvalidAmountError, parse_cop
from tests.test_bot import USER, button_update, buttons, text_update, texts

TODAY = local_today()


@pytest.fixture
def linked(db: Session, company: Company, employee: Employee) -> Employee:
    """Empleado ya vinculado a Telegram, con las categorías por defecto."""
    employee.telegram_user_id = USER
    employee.telegram_chat_id = USER
    catalog.ensure_default_categories(db, company.id)
    db.add(BotSession(telegram_user_id=USER, employee_id=employee.id, state="menu", data={}))
    db.flush()
    return employee


def location_update(lat: float, lon: float) -> dict:
    update = text_update("x")
    del update["message"]["text"]
    update["message"]["location"] = {"latitude": lat, "longitude": lon}
    return update


def say(db: Session, *inputs) -> list[dict]:
    """Envía varios mensajes seguidos. str = texto/comando; ("btn", data); ("loc", lat, lon)."""
    actions: list[dict] = []
    for item in inputs:
        if isinstance(item, str):
            update = text_update(item)
        elif item[0] == "btn":
            update = button_update(item[1])
        else:
            update = location_update(item[1], item[2])
        actions = engine.handle_update(db, update)
    return actions


def state(db: Session) -> str:
    return db.get(BotSession, USER).state


def category_button(db: Session, company: Company, name: str = "Alimentación") -> tuple[str, str]:
    category = next(c for c in catalog.list_categories(db, company.id) if c.name == name)
    return ("btn", f"{flow.CB_CATEGORY}{category.id}")


def fill_invoice(db: Session, company: Company, *, nit="800.197.268-4", name="Restaurante El Cliente",
                 invoice="FE-1") -> list[dict]:
    """Recorre los pasos desde la foto hasta el resumen (sin foto; nombre y RUT si el NIT es nuevo)."""
    steps = [("btn", flow.CB_NO_PHOTO), "Almuerzo con cliente", "45.000", ("btn", flow.CB_SKIP),
             ("btn", flow.CB_TODAY), nit]
    if name:
        steps += [name, ("btn", flow.CB_SKIP)]  # nombre del proveedor nuevo y RUT omitido
    steps += [invoice, category_button(db, company), ("btn", flow.CB_SKIP)]
    return say(db, *steps)


@pytest.fixture
def open_viatico(db: Session, linked: Employee, admin: AdminUser) -> Viatico:
    return viaticos.open_viatico(db, linked, initial_amount=Decimal("500000"), purpose="Viaje a Medellín",
                                 source=MovementSource.ADMIN, actor=Actor.from_admin(admin))


# --- CA-1: pregunta de viáticos ---


def test_ca1_without_viaticos_asks_exact_question(db: Session, linked: Employee) -> None:
    actions = say(db, ("btn", engine.CB_EXPENSE))

    assert texts(actions) == ["💰 ¿Contabas con dinero previo para realizar estos gastos (viáticos)?"]
    assert buttons(actions) == [flow.CB_YES, flow.CB_NO]


def test_ca1_with_open_viaticos_offers_each_one(db, linked, open_viatico, admin) -> None:
    second = viaticos.open_viatico(db, linked, initial_amount=Decimal("100000"), purpose="Bogotá",
                                   source=MovementSource.ADMIN, actor=Actor.from_admin(admin))

    actions = say(db, ("btn", engine.CB_EXPENSE))

    options = buttons(actions)
    assert f"{flow.CB_VIATICO}{open_viatico.id}" in options
    assert f"{flow.CB_VIATICO}{second.id}" in options
    assert options[-2:] == [flow.CB_NEW_VIATICO, flow.CB_OWN_MONEY]
    question = next(a for a in actions if a["method"] == "sendMessage")  # la 1.ª es answerCallbackQuery
    labels = [b["text"] for row in question["payload"]["reply_markup"]["inline_keyboard"] for b in row]
    assert "💼 Viaje a Medellín · disponible $500.000" in labels


# --- CA-2: dinero previo sí/no ---


def test_ca2_yes_creates_employee_viatico_on_confirm(db: Session, linked: Employee, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_YES))
    assert state(db) == flow.INITIAL_AMOUNT
    say(db, "500.000", "Viaje a Cali")
    summary = texts(fill_invoice(db, company))[-1]
    assert "Viático nuevo «Viaje a Cali» ($500.000 iniciales)" in summary
    assert db.scalars(select(Viatico)).all() == []  # nada se guarda antes de confirmar

    actions = say(db, ("btn", flow.CB_CONFIRM))

    viatico = db.scalars(select(Viatico)).one()
    assert viatico.purpose == "Viaje a Cali"
    assert viatico.movements[0].source == MovementSource.EMPLOYEE
    assert viatico.movements[0].amount == Decimal("500000")
    expense = db.scalars(select(Expense)).one()
    assert expense.viatico_id == viatico.id
    assert "Disponible en «Viaje a Cali»: <b>$455.000</b>" in texts(actions)[0]


def test_ca2_no_means_own_money_and_reimbursable(db: Session, linked: Employee, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))
    fill_invoice(db, company)

    actions = say(db, ("btn", flow.CB_CONFIRM))

    expense = db.scalars(select(Expense)).one()
    assert expense.payment_source == PaymentSource.OWN_MONEY
    assert expense.is_reimbursable is True
    assert "reembolsable" in texts(actions)[0]


def test_existing_viatico_is_used(db, linked, open_viatico, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", f"{flow.CB_VIATICO}{open_viatico.id}"))
    fill_invoice(db, company)

    say(db, ("btn", flow.CB_CONFIRM))

    assert db.scalars(select(Expense)).one().viatico_id == open_viatico.id
    assert len(db.scalars(select(Viatico)).all()) == 1  # no se creó otro


# --- CA-3: montos colombianos ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45000", "45000.00"), ("45.000", "45000.00"), ("$45.000", "45000.00"), ("$ 45.000 COP", "45000.00"),
        ("1.234.567,50", "1234567.50"), ("1,234,567.50", "1234567.50"), ("45000,5", "45000.50"),
        ("45.5", "45.50"), ("150.000.000", "150000000.00"),
    ],
)
def test_ca3_parses_colombian_amounts(text: str, expected: str) -> None:
    assert parse_cop(text) == Decimal(expected)


@pytest.mark.parametrize("text", ["cuarenta mil", "0", "-5000", "45.00.0", "1.23.456", "", "$", "12,34,5"])
def test_ca3_rejects_invalid_amounts(text: str) -> None:
    with pytest.raises(InvalidAmountError):
        parse_cop(text)


def test_ca3_bad_amount_is_asked_again(db: Session, linked: Employee) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Taxi al aeropuerto")

    actions = say(db, "cuarenta mil")

    assert texts(actions) == [msg.EXP_BAD_AMOUNT, msg.EXP_ASK_TOTAL]
    assert state(db) == flow.TOTAL


# --- CA-4: fecha ---


def test_ca4_date_parsing() -> None:
    assert parse_invoice_date("hoy") == TODAY
    assert parse_invoice_date("Ayer") == TODAY - timedelta(days=1)
    assert parse_invoice_date("03/11/2026") == date(2026, 11, 3)
    assert parse_invoice_date("3-11-26") == date(2026, 11, 3)
    assert parse_invoice_date("2026-11-03") == date(2026, 11, 3)
    for bad in ["32/01/2026", "31/02/2026", "mañana", (TODAY + timedelta(days=1)).strftime("%d/%m/%Y")]:
        with pytest.raises(InvalidDateError):
            parse_invoice_date(bad)


def test_ca4_future_date_is_asked_again(db: Session, linked: Employee) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Taxi", "20000", ("btn", flow.CB_SKIP))

    actions = say(db, (TODAY + timedelta(days=3)).strftime("%d/%m/%Y"))

    assert texts(actions)[0] == "La fecha de la factura no puede ser futura."
    assert state(db) == flow.ISSUE_DATE


# --- CA-5: NIT ---


def _to_nit_step(db) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Taxi", "20000", ("btn", flow.CB_SKIP),
        ("btn", flow.CB_TODAY))
    assert state(db) == flow.NIT


def test_ca5_wrong_dv_is_explained(db: Session, linked: Employee) -> None:
    _to_nit_step(db)

    actions = say(db, "800197268-9")

    assert "dígito de verificación" in texts(actions)[0]
    assert state(db) == flow.NIT


def test_ca5_known_supplier_skips_name(db: Session, linked: Employee, company: Company) -> None:
    db.add(Supplier(company_id=company.id, nit="800197268", dv="4", name="DIAN"))
    db.flush()
    _to_nit_step(db)

    actions = say(db, "800.197.268-4")

    # Muestra el nombre Y hace la siguiente pregunta (no deja la conversación en el aire).
    assert texts(actions) == ["Establecimiento: <b>DIAN</b> ✅", msg.EXP_ASK_INVOICE]
    assert state(db) == flow.INVOICE_NUMBER


def test_ca5_new_supplier_asks_name(db: Session, linked: Employee) -> None:
    _to_nit_step(db)

    say(db, "890903938-8")

    assert state(db) == flow.SUPPLIER_NAME
    say(db, "Bancolombia")
    assert state(db) == flow.RUT
    say(db, ("btn", flow.CB_SKIP))
    assert state(db) == flow.INVOICE_NUMBER


def test_ca5_nit_can_be_skipped(db: Session, linked: Employee, company: Company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))

    summary = texts(fill_invoice(db, company, nit=("btn", flow.CB_SKIP), name=None))[-1]

    assert "<b>Establecimiento:</b> Sin NIT" in summary


# --- CA-6: ubicación ---


def _to_location_step(db, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Taxi", "20000", ("btn", flow.CB_SKIP),
        ("btn", flow.CB_TODAY), ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP), category_button(db, company, "Transporte"))
    assert state(db) == flow.LOCATION


def test_ca6_location_prompt_uses_telegram_location_button(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Taxi", "20000", ("btn", flow.CB_SKIP),
        ("btn", flow.CB_TODAY), ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP))

    actions = say(db, category_button(db, company, "Transporte"))

    keyboard = actions[-1]["payload"]["reply_markup"]["keyboard"]
    assert keyboard[0][0] == {"text": msg.EXP_SHARE_LOCATION, "request_location": True}


def test_ca6_shared_location_is_saved_as_telegram(db, linked, company) -> None:
    _to_location_step(db, company)

    actions = say(db, ("loc", 6.2442031, -75.5812119))

    assert actions[0]["payload"]["reply_markup"] == {"remove_keyboard": True}
    assert "Compartida ✅" in texts(actions)[-1]
    say(db, ("btn", flow.CB_CONFIRM))
    expense = db.scalars(select(Expense)).one()
    assert expense.location_source == LocationSource.TELEGRAM
    assert expense.location_lat == Decimal("6.244203")


def test_ca6_skipped_location_is_not_available(db, linked, company) -> None:
    _to_location_step(db, company)

    actions = say(db, msg.SKIP)  # el botón "Omitir" del teclado llega como texto

    assert "<b>Ubicación:</b> No disponible" in texts(actions)[-1]
    say(db, ("btn", flow.CB_CONFIRM))
    expense = db.scalars(select(Expense)).one()
    assert expense.location_source == LocationSource.NONE
    assert expense.location_lat is None


# --- CA-7: resumen y edición ---


def test_ca7_summary_shows_everything(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))

    summary = texts(fill_invoice(db, company))[-1]

    for expected in ["Almuerzo con cliente", "$45.000", TODAY.strftime("%d/%m/%Y"),
                     "Restaurante El Cliente (NIT 800197268-4)", "FE-1", "Alimentación",
                     "Dinero propio (reembolsable)", "No disponible"]:
        assert expected in summary
    assert state(db) == flow.CONFIRM


def test_ca7_edit_a_field_and_return_to_summary(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))
    fill_invoice(db, company)

    say(db, ("btn", flow.CB_EDIT), ("btn", f"{flow.CB_FIELD}total"))
    assert state(db) == flow.TOTAL
    actions = say(db, "52.300")

    assert state(db) == flow.CONFIRM
    assert "$52.300" in texts(actions)[-1]


def test_ca7_edit_nit_to_unknown_asks_name_then_summary(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))
    fill_invoice(db, company)

    say(db, ("btn", flow.CB_EDIT), ("btn", f"{flow.CB_FIELD}nit"), "890903938-8")
    assert state(db) == flow.SUPPLIER_NAME
    actions = say(db, "Bancolombia", ("btn", flow.CB_SKIP))  # nombre y RUT omitido

    assert state(db) == flow.CONFIRM
    assert "Bancolombia (NIT 890903938-8)" in texts(actions)[-1]


# --- CA-8: confirmar ---


def test_ca8_confirm_submits_expense_audited_as_employee(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))
    fill_invoice(db, company)

    actions = say(db, ("btn", flow.CB_CONFIRM))

    expense = db.scalars(select(Expense)).one()
    assert expense.status == ExpenseStatus.SUBMITTED
    assert expense.total == Decimal("45000")
    assert expense.description == "Almuerzo con cliente"
    assert expense.issue_date == TODAY
    assert expense.invoice_number == "FE-1"
    actors = {row.actor_type for row in db.scalars(select(AuditLog).where(AuditLog.entity_type == "expense"))}
    assert actors == {"employee"}
    assert texts(actions)[0].startswith(msg.EXP_SAVED)
    assert state(db) == "menu"
    assert db.get(BotSession, USER).data == {}


def test_unit_price_creates_an_item(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Galones de gasolina", "60.000", "15.000",
        ("btn", flow.CB_TODAY), ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP),
        category_button(db, company, "Combustible"), ("btn", flow.CB_SKIP), ("btn", flow.CB_CONFIRM))

    item = db.scalars(select(Expense)).one().items[0]

    assert (item.quantity, item.unit_price, item.line_total) == (Decimal("4"), Decimal("15000"), Decimal("60000"))


def test_unit_price_greater_than_total_is_rejected(db, linked) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Taxi", "20.000")

    assert texts(say(db, "30.000"))[0] == msg.EXP_UNIT_GT_TOTAL


# --- CA-9: factura duplicada ---


def test_ca9_duplicate_is_reported_and_can_be_corrected(db, linked, company, admin) -> None:
    expenses.create_expense(db, linked, {"total": Decimal("1"), "invoice_number": "FE-1"},
                            supplier_nit="800197268-4", supplier_name="Restaurante El Cliente",
                            actor=Actor.from_admin(admin))
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_YES), "300.000", ("btn", flow.CB_SKIP))
    fill_invoice(db, company, name=None)  # el NIT ya es conocido: no pide nombre

    actions = say(db, ("btn", flow.CB_CONFIRM))

    assert texts(actions)[0].startswith("⚠️ Ya existe un gasto registrado con esa factura")
    assert len(db.scalars(select(Expense)).all()) == 1  # no se creó el duplicado
    assert db.scalars(select(Viatico)).all() == []  # ni el viático nuevo (todo o nada)
    assert state(db) == flow.CONFIRM

    say(db, ("btn", flow.CB_EDIT), ("btn", f"{flow.CB_FIELD}invoice_number"), "FE-2", ("btn", flow.CB_CONFIRM))

    assert len(db.scalars(select(Expense)).all()) == 2
    assert len(db.scalars(select(Viatico)).all()) == 1


# --- CA-10: cancelar y botones viejos ---


def test_ca10_cancel_discards_draft(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_YES), "500.000", ("btn", flow.CB_SKIP))
    fill_invoice(db, company)

    actions = say(db, "/cancelar")

    assert texts(actions)[0] == msg.EXP_CANCELLED
    assert db.scalars(select(Expense)).all() == []
    assert db.scalars(select(Viatico)).all() == []
    assert state(db) == "menu"


def test_ca10_cancel_button_in_summary(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))
    fill_invoice(db, company)

    say(db, ("btn", flow.CB_CANCEL))

    assert db.scalars(select(Expense)).all() == [] and state(db) == "menu"


def test_ca10_old_buttons_do_not_break_the_flow(db, linked) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO))  # ahora pide la descripción

    actions = say(db, ("btn", flow.CB_TODAY))  # botón de otro paso

    assert texts(actions) == [msg.EXP_USE_LATEST, msg.EXP_ASK_DESCRIPTION]
    assert state(db) == flow.DESCRIPTION


def test_ca10_button_of_closed_viatico_is_rejected(db, linked, open_viatico, admin) -> None:
    say(db, ("btn", engine.CB_EXPENSE))
    viaticos.close_viatico(db, open_viatico, Actor.from_admin(admin))

    actions = say(db, ("btn", f"{flow.CB_VIATICO}{open_viatico.id}"))

    assert texts(actions)[0] == msg.EXP_USE_LATEST
    assert state(db) == flow.WHICH_MONEY


def test_queries_do_not_interrupt_the_flow(db, linked) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO))

    actions = say(db, "/saldo")

    assert texts(actions) == [msg.NO_OPEN_VIATICOS, msg.EXP_ASK_DESCRIPTION]
    assert state(db) == flow.DESCRIPTION


# --- CA-11: el progreso sobrevive a un reinicio ---


def test_ca11_progress_survives_restart(db, linked, company) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO), ("btn", flow.CB_NO_PHOTO), "Almuerzo", "45.000")
    db.expire_all()  # simula que la API se reinició: todo se vuelve a leer de la base

    say(db, ("btn", flow.CB_SKIP), ("btn", flow.CB_TODAY), ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP),
        category_button(db, company), ("btn", flow.CB_SKIP), ("btn", flow.CB_CONFIRM))

    assert db.scalars(select(Expense)).one().total == Decimal("45000")
