"""Paso 4.4: consulta y corrección de gastos por el empleado (CA-1 a CA-10)."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import engine, expense_flow as exp, fix_flow as fix
from app.bot import messages as msg
from app.models import AdminUser, AuditLog, BotSession, Company, Document, Employee, Expense, Supplier
from app.models.enums import ExpenseStatus, MovementSource
from app.services import expenses, viaticos
from app.services.audit import Actor
from app.utils.dates import local_today
from tests.test_bot import USER, buttons, texts
from tests.test_bot_expense import category_button, linked, say, state  # noqa: F401
from tests.test_bot_files import deliver, photo_update, store_actions
from tests.test_documents import JPEG, storage  # noqa: F401

D = Decimal


def make_expense(db: Session, employee: Employee, *, submit: bool = True, **fields) -> Expense:
    actor = Actor.from_employee(employee)
    data = {"description": "Almuerzo", "total": D("45000"), "issue_date": date(2026, 11, 2), **fields}
    expense = expenses.create_expense(db, employee, data, actor=actor)
    if submit:
        expenses.submit(db, expense, actor)
    return expense


def open_detail(db: Session, expense: Expense) -> list[dict]:
    return say(db, ("btn", f"{fix.CB_OPEN}{expense.id}"))


def edit(db: Session, expense: Expense, field: str) -> None:
    open_detail(db, expense)
    say(db, ("btn", fix.CB_EDIT), ("btn", f"{fix.CB_FIELD}{field}"))
    assert state(db) == fix.FIELDS[field]


def detail_text(actions: list[dict]) -> str:
    return texts(actions)[-1]


# --- CA-1: lista ---


def test_ca1_list_shows_expenses_as_buttons(db: Session, linked: Employee, admin: AdminUser) -> None:
    open_one = make_expense(db, linked, description="Taxi aeropuerto", total=D("20000"))
    approved = make_expense(db, linked, description="Hotel", total=D("180000"))
    expenses.approve(db, approved, Actor.from_admin(admin))

    actions = say(db, ("btn", engine.CB_EXPENSES))

    assert texts(actions) == [msg.FIX_LIST_HEADER]
    assert set(buttons(actions)) == {f"{fix.CB_OPEN}{open_one.id}", f"{fix.CB_OPEN}{approved.id}"}
    labels = [b["text"] for row in actions[-1]["payload"]["reply_markup"]["inline_keyboard"] for b in row]
    assert "02/11 · Taxi aeropuerto · $20.000 · enviado" in labels
    assert "02/11 · Hotel · $180.000 · aprobado 🔒" in labels


def test_ca1_no_expenses(db: Session, linked: Employee) -> None:
    assert texts(say(db, "/gastos"))[0] == msg.NO_EXPENSES


# --- CA-2: detalle ---


def test_ca2_detail_shows_data_and_options(db: Session, linked: Employee, company: Company) -> None:
    db.add(Supplier(company_id=company.id, nit="800197268", dv="4", name="Restaurante"))
    db.flush()
    expense = make_expense(db, linked)
    expenses.update_expense(db, expense, {}, version=expense.version, actor=Actor.from_employee(linked),
                            supplier_nit="800197268-4")

    actions = open_detail(db, expense)

    text = detail_text(actions)
    for expected in ["Gasto del 02/11/2026", "Almuerzo", "$45.000", "Restaurante (NIT 800197268-4)", "enviado"]:
        assert expected in text
    assert fix.CB_EDIT in buttons(actions)
    assert fix.CB_RESUBMIT not in buttons(actions)  # ya está enviado
    assert state(db) == fix.DETAIL


def test_ca2_rejected_shows_reason_and_resubmit(db, linked, admin) -> None:
    expense = make_expense(db, linked)
    expenses.reject(db, expense, Actor.from_admin(admin), "Falta la foto de la factura")

    actions = open_detail(db, expense)

    assert "Motivo del rechazo:</b> Falta la foto de la factura" in detail_text(actions)
    assert {fix.CB_EDIT, fix.CB_RESUBMIT} <= set(buttons(actions))


# --- CA-3: correcciones ---


def test_ca3_correct_total(db: Session, linked: Employee) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "total")

    actions = say(db, "52.300")

    assert texts(actions)[0] == msg.FIX_SAVED
    assert "$52.300" in detail_text(actions)
    assert expense.total == D("52300")
    assert state(db) == fix.DETAIL


def test_ca3_correct_description_date_invoice_and_category(db, linked, company) -> None:
    expense = make_expense(db, linked, invoice_number="FE-1")

    edit(db, expense, "description"); say(db, "Almuerzo con cliente")
    edit(db, expense, "issue_date"); say(db, ("btn", exp.CB_TODAY))
    edit(db, expense, "invoice_number"); say(db, ("btn", exp.CB_SKIP))  # omitir = quitarlo
    edit(db, expense, "category"); say(db, category_button(db, company, "Transporte"))

    assert expense.description == "Almuerzo con cliente"
    assert expense.issue_date == local_today()
    assert expense.invoice_number is None
    assert expense.category_id is not None


def test_ca3_new_nit_asks_name(db: Session, linked: Employee) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "nit")

    say(db, "890903938-8")
    assert state(db) == fix.SUPPLIER_NAME
    actions = say(db, "Bancolombia")

    assert "Bancolombia (NIT 890903938-8)" in detail_text(actions)
    assert db.get(Supplier, expense.supplier_id).name == "Bancolombia"


def test_ca3_add_photo(db: Session, linked: Employee, storage) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "photo")

    request = store_actions(engine.handle_update(db, photo_update()))
    assert request and request[0]["payload"]["purpose"] == "invoice"
    actions = deliver(db, storage, JPEG)

    assert texts(actions)[0] == msg.FIX_PHOTO_ADDED
    assert "<b>Fotos:</b> 1" in detail_text(actions)
    assert db.scalars(select(Document)).one().expense_id == expense.id


# --- CA-4: bloqueados ---


def test_ca4_approved_has_no_edit_button(db, linked, admin) -> None:
    expense = make_expense(db, linked)
    expenses.approve(db, expense, Actor.from_admin(admin))

    actions = open_detail(db, expense)

    assert fix.CB_EDIT not in buttons(actions)
    assert "🔒 Este gasto ya no se puede modificar." in detail_text(actions)


def test_ca4_stale_edit_button_after_approval(db, linked, admin) -> None:
    expense = make_expense(db, linked)
    open_detail(db, expense)
    expenses.approve(db, expense, Actor.from_admin(admin))  # el admin aprueba mientras tanto

    actions = say(db, ("btn", fix.CB_EDIT))

    assert texts(actions)[0].startswith("🔒")
    assert state(db) == fix.DETAIL


def test_ca4_approved_while_typing_the_value(db, linked, admin) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "total")
    expenses.approve(db, expense, Actor.from_admin(admin))

    actions = say(db, "1.000")

    assert texts(actions)[0].startswith("🔒")
    assert expense.total == D("45000")  # no cambió


def test_ca4_closed_viatico_locks_expense(db, linked, admin) -> None:
    actor = Actor.from_admin(admin)
    viatico = viaticos.open_viatico(db, linked, initial_amount=D("100000"), purpose="Cali",
                                    source=MovementSource.ADMIN, actor=actor)
    expense = make_expense(db, linked, viatico_id=viatico.id)
    expenses.reject(db, expense, actor, "Factura ilegible")
    viaticos.close_viatico(db, viatico, actor)

    assert fix.CB_EDIT not in buttons(open_detail(db, expense))


# --- CA-5: historial ---


def test_ca5_correction_is_audited_as_employee(db: Session, linked: Employee) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "total")

    say(db, "50.000")

    row = db.scalars(select(AuditLog).where(AuditLog.field == "total")).one()
    assert (row.old_value, row.new_value) == ("45000.00", "50000.00")
    assert row.actor_type == "employee" and row.actor_id == str(linked.id)


# --- CA-6: edición simultánea ---


def test_ca6_admin_changed_it_meanwhile(db, linked, admin) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "total")
    expenses.update_expense(db, expense, {"description": "Corregido por el admin"},
                            version=expense.version, actor=Actor.from_admin(admin))

    actions = say(db, "99.000")

    assert texts(actions)[0] == msg.FIX_CONFLICT
    assert expense.total == D("45000")
    assert expense.description == "Corregido por el admin"
    assert "Corregido por el admin" in detail_text(actions)  # le muestra la versión actual


# --- CA-7: reenviar un rechazado ---


def test_ca7_fix_and_resubmit_rejected(db, linked, admin) -> None:
    expense = make_expense(db, linked)
    expenses.reject(db, expense, Actor.from_admin(admin), "El total no coincide")
    edit(db, expense, "total")
    say(db, "47.000")

    actions = say(db, ("btn", fix.CB_RESUBMIT))

    assert texts(actions)[0] == msg.FIX_RESUBMITTED
    assert expense.status == ExpenseStatus.SUBMITTED
    assert expense.rejection_reason is None


# --- CA-8: solo los propios ---


def test_ca8_foreign_expense_is_not_found(db, linked, company) -> None:
    other = Employee(company_id=company.id, first_name="Ana", last_name="G", internal_id="E2")
    db.add(other)
    db.flush()
    foreign = make_expense(db, other)

    actions = open_detail(db, foreign)

    assert texts(actions)[0] == msg.FIX_NOT_FOUND
    assert state(db) == "menu"


# --- CA-9: duplicados ---


def test_ca9_duplicate_invoice_is_rejected(db, linked, company) -> None:
    make_expense(db, linked, invoice_number="FE-1")
    second = make_expense(db, linked, invoice_number="FE-2")
    for e in db.scalars(select(Expense)):
        expenses.update_expense(db, e, {}, version=e.version, actor=Actor.from_employee(linked),
                                supplier_nit="800197268-4", supplier_name="Restaurante")
    edit(db, second, "invoice_number")

    actions = say(db, "FE-1")

    assert texts(actions)[0].startswith("⚠️ Ya existe un gasto registrado con esa factura")
    assert second.invoice_number == "FE-2"


# --- CA-10: mismas validaciones que el registro ---


def test_ca10_same_validation_as_registration(db: Session, linked: Employee) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "total")

    actions = say(db, "cuarenta mil")

    assert texts(actions) == [msg.EXP_BAD_AMOUNT, msg.EXP_ASK_TOTAL]
    assert state(db) == fix.TOTAL


def test_cancel_returns_to_menu(db: Session, linked: Employee) -> None:
    expense = make_expense(db, linked)
    edit(db, expense, "total")

    say(db, "/cancelar")

    assert state(db) == "menu"
    assert db.get(BotSession, USER).data == {}
