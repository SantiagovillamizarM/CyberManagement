"""CA-2 a CA-5: viáticos y saldos."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.models import AdminUser, Employee, Viatico
from app.models.enums import ExpenseStatus, MovementSource, MovementType
from app.services import expenses, viaticos
from app.services.audit import Actor
from app.services.viaticos import Settlement

D = Decimal


@pytest.fixture
def actor(admin: AdminUser) -> Actor:
    return Actor.from_admin(admin)


@pytest.fixture
def viatico(db: Session, employee: Employee, actor: Actor) -> Viatico:
    return viaticos.open_viatico(
        db, employee, initial_amount=D("500000"), purpose="Viaje a Medellín",
        source=MovementSource.EMPLOYEE, actor=actor,
    )


def spend(db, employee, viatico, actor, total: str, status=ExpenseStatus.SUBMITTED, **extra):
    expense = expenses.create_expense(
        db, employee,
        {"total": D(total), "description": "Gasto", "issue_date": date(2026, 11, 1),
         "viatico_id": viatico.id, **extra},
        actor=actor,
    )
    expense.status = status
    db.flush()
    return expense


def test_ca2_balance_example(db: Session, employee, viatico, actor) -> None:
    """500.000 + 200.000 − 50.000 de reintegro − 320.000 gastados = 330.000."""
    viaticos.add_movement(db, viatico, type=MovementType.ADDITIONAL, amount=D("200000"),
                          note=None, source=MovementSource.ADMIN, actor=actor)
    viaticos.add_movement(db, viatico, type=MovementType.REFUND, amount=D("50000"),
                          note=None, source=MovementSource.ADMIN, actor=actor)
    spend(db, employee, viatico, actor, "120000")
    spend(db, employee, viatico, actor, "200000", status=ExpenseStatus.APPROVED)

    balance = viaticos.compute_balance(db, viatico)

    assert balance.delivered == D("700000")
    assert balance.refunded == D("50000")
    assert balance.spent == D("320000")
    assert balance.spent_approved == D("200000")
    assert balance.remaining == D("330000")
    assert balance.settlement == Settlement.EMPLOYEE_MUST_RETURN


def test_ca3_rejected_deleted_and_draft_do_not_count(db: Session, employee, viatico, actor) -> None:
    spend(db, employee, viatico, actor, "100000", status=ExpenseStatus.REJECTED)
    spend(db, employee, viatico, actor, "100000", status=ExpenseStatus.DRAFT)
    deleted = spend(db, employee, viatico, actor, "100000")
    expenses.delete_expense(db, deleted, actor)

    assert viaticos.compute_balance(db, viatico).spent == D("0")


def test_ca4_company_must_reimburse_when_overspent(db: Session, employee, viatico, actor) -> None:
    spend(db, employee, viatico, actor, "620000")

    balance = viaticos.compute_balance(db, viatico)

    assert balance.remaining == D("-120000")
    assert balance.settlement == Settlement.COMPANY_MUST_REIMBURSE


def test_ca4_balanced(db: Session, employee, viatico, actor) -> None:
    spend(db, employee, viatico, actor, "500000")

    assert viaticos.compute_balance(db, viatico).settlement == Settlement.BALANCED


def test_negative_adjustment_reduces_delivered(db: Session, viatico, actor) -> None:
    viaticos.add_movement(db, viatico, type=MovementType.ADJUSTMENT, amount=D("-20000"),
                          note="Error de digitación", source=MovementSource.ADMIN, actor=actor)

    assert viaticos.compute_balance(db, viatico).delivered == D("480000")


@pytest.mark.parametrize(
    ("movement_type", "amount", "note"),
    [
        (MovementType.INITIAL, "100", None),  # el inicial solo al abrir
        (MovementType.ADDITIONAL, "-100", None),
        (MovementType.ADJUSTMENT, "-100", None),  # ajuste sin nota
        (MovementType.ADJUSTMENT, "0", "nota"),
    ],
)
def test_invalid_movements_are_rejected(db, viatico, actor, movement_type, amount, note) -> None:
    with pytest.raises(viaticos.ViaticoRuleError):
        viaticos.add_movement(db, viatico, type=movement_type, amount=D(amount), note=note,
                              source=MovementSource.ADMIN, actor=actor)


def test_ca5_cannot_close_with_unresolved_expenses(db: Session, employee, viatico, actor) -> None:
    spend(db, employee, viatico, actor, "100000")  # enviado, sin aprobar

    with pytest.raises(viaticos.ViaticoRuleError, match="sin aprobar"):
        viaticos.close_viatico(db, viatico, actor)


def test_ca5_closed_viatico_rejects_movements_and_expenses(db: Session, employee, viatico, actor) -> None:
    spend(db, employee, viatico, actor, "100000", status=ExpenseStatus.APPROVED)
    balance = viaticos.close_viatico(db, viatico, actor)
    assert balance.remaining == D("400000")

    with pytest.raises(viaticos.ViaticoClosedError):
        viaticos.add_movement(db, viatico, type=MovementType.ADDITIONAL, amount=D("1"),
                              note=None, source=MovementSource.ADMIN, actor=actor)
    with pytest.raises(expenses.ExpenseRuleError, match="cerrado"):
        spend(db, employee, viatico, actor, "1000")


def test_admin_can_correct_expense_of_closed_viatico(db: Session, employee, viatico, actor) -> None:
    """Caso encontrado en la prueba en vivo: "todo debe ser editable" por el administrador."""
    expense = spend(db, employee, viatico, actor, "100000", status=ExpenseStatus.APPROVED)
    viaticos.close_viatico(db, viatico, actor)

    expenses.update_expense(db, expense, {"description": "Corregido"}, version=expense.version, actor=actor)

    assert expense.description == "Corregido"


def test_employee_cannot_touch_expense_of_closed_viatico(db: Session, employee, viatico, actor) -> None:
    expense = spend(db, employee, viatico, actor, "100000")
    expenses.reject(db, expense, actor, "Factura ilegible")
    viaticos.close_viatico(db, viatico, actor)
    as_employee = Actor.from_employee(employee)

    with pytest.raises(expenses.ExpenseLockedError, match="cerrado"):
        expenses.update_expense(db, expense, {"total": D("1")}, version=expense.version, actor=as_employee)
    # Reenviarlo cambiaría el saldo de un viático ya liquidado.
    with pytest.raises(expenses.ExpenseRuleError, match="cerrado"):
        expenses.submit(db, expense, actor)


def test_employee_can_have_several_open_viaticos(db: Session, company, employee, viatico, actor) -> None:
    second = viaticos.open_viatico(db, employee, initial_amount=D("100000"), purpose="Bogotá",
                                   source=MovementSource.ADMIN, actor=actor)

    open_ones = viaticos.list_viaticos(db, company.id, employee_id=employee.id)

    assert {v.id for v in open_ones} == {viatico.id, second.id}


def test_viatico_belongs_to_its_employee(db: Session, company, viatico, actor) -> None:
    other = Employee(company_id=company.id, first_name="Ana", last_name="G", internal_id="E2")
    db.add(other)
    db.flush()

    with pytest.raises(expenses.ExpenseRuleError, match="no pertenece"):
        spend(db, other, viatico, actor, "1000")
