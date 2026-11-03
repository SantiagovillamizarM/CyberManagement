"""Pruebas de las reglas que protege la propia base de datos."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.db.base import Base
from app.models import (
    Company,
    Document,
    Employee,
    Expense,
    ExpenseItem,
    RegistrationCode,
    Viatico,
    ViaticoMovement,
)
from app.models.enums import DocumentKind, MovementSource, MovementType


def test_migrations_match_models(db: Session) -> None:
    """Si alguien cambia un modelo y olvida crear la migración, esta prueba falla."""
    context = MigrationContext.configure(db.connection(), opts={"compare_type": True})

    differences = compare_metadata(context, Base.metadata)

    assert differences == []


def test_telegram_account_can_link_to_only_one_employee(
    db: Session, company: Company, employee: Employee
) -> None:
    employee.telegram_user_id = 111222333
    other = Employee(
        company_id=company.id,
        first_name="Ana",
        last_name="Gómez",
        internal_id="EMP-002",
        telegram_user_id=111222333,
    )
    db.add(other)

    with pytest.raises(IntegrityError):
        db.flush()


def test_employee_can_have_only_one_active_registration_code(
    db: Session, employee: Employee
) -> None:
    expires = datetime.now(UTC) + timedelta(hours=72)
    db.add(RegistrationCode(employee_id=employee.id, code_hash="a" * 64, code_hint="JHON-…AAAA", expires_at=expires))
    db.flush()

    db.add(RegistrationCode(employee_id=employee.id, code_hash="b" * 64, code_hint="JHON-…BBBB", expires_at=expires))
    with pytest.raises(IntegrityError):
        db.flush()


def test_new_code_allowed_after_previous_is_revoked(db: Session, employee: Employee) -> None:
    expires = datetime.now(UTC) + timedelta(hours=72)
    first = RegistrationCode(employee_id=employee.id, code_hash="a" * 64, code_hint="x", expires_at=expires)
    db.add(first)
    db.flush()

    first.revoked_at = datetime.now(UTC)
    db.add(RegistrationCode(employee_id=employee.id, code_hash="b" * 64, code_hint="y", expires_at=expires))
    db.flush()  # no debe fallar


def _expense(company: Company, employee: Employee, **fields) -> Expense:
    return Expense(company_id=company.id, employee_id=employee.id, **fields)


def test_duplicate_cufe_is_rejected(db: Session, company: Company, employee: Employee) -> None:
    db.add(_expense(company, employee, cufe="CUFE-123", total=Decimal("50000")))
    db.flush()

    db.add(_expense(company, employee, cufe="CUFE-123", total=Decimal("50000")))
    with pytest.raises(IntegrityError):
        db.flush()


def test_deleted_expense_does_not_block_its_cufe(
    db: Session, company: Company, employee: Employee
) -> None:
    db.add(_expense(company, employee, cufe="CUFE-123", deleted_at=datetime.now(UTC)))
    db.flush()

    db.add(_expense(company, employee, cufe="CUFE-123"))
    db.flush()  # no debe fallar


def test_money_is_stored_without_rounding_errors(
    db: Session, company: Company, employee: Employee
) -> None:
    expense = _expense(company, employee, subtotal=Decimal("84033.61"), tax_total=Decimal("15966.39"))
    expense.total = Decimal("100000.00")
    expense.items = [
        ExpenseItem(description="Almuerzo", quantity=Decimal("3"), unit_price=Decimal("33333.33"))
    ]
    db.add(expense)
    db.flush()
    db.expire_all()

    stored = db.scalars(select(Expense).where(Expense.id == expense.id)).one()

    assert stored.subtotal + stored.tax_total == Decimal("100000.00")
    assert stored.items[0].unit_price == Decimal("33333.33")


def test_negative_total_is_rejected(db: Session, company: Company, employee: Employee) -> None:
    db.add(_expense(company, employee, total=Decimal("-1")))

    with pytest.raises(IntegrityError):
        db.flush()


def test_concurrent_edit_is_detected(db: Session, company: Company, employee: Employee) -> None:
    expense = _expense(company, employee, description="Taxi")
    db.add(expense)
    db.flush()
    # Otra persona guarda un cambio mientras tanto (sube la versión).
    db.execute(text("UPDATE expenses SET version = version + 1 WHERE id = :id"), {"id": expense.id})

    expense.description = "Taxi aeropuerto"
    with pytest.raises(StaleDataError):
        db.flush()


@pytest.mark.parametrize(
    ("movement_type", "amount", "allowed"),
    [
        (MovementType.INITIAL, Decimal("500000"), True),
        (MovementType.ADJUSTMENT, Decimal("-20000"), True),
        (MovementType.ADDITIONAL, Decimal("-100"), False),
        (MovementType.INITIAL, Decimal("0"), False),
    ],
)
def test_viatico_movement_amount_rules(
    db: Session, employee: Employee, movement_type: MovementType, amount: Decimal, allowed: bool
) -> None:
    viatico = Viatico(employee_id=employee.id, purpose="Viaje a Medellín")
    viatico.movements = [
        ViaticoMovement(type=movement_type, amount=amount, source=MovementSource.EMPLOYEE)
    ]
    db.add(viatico)

    if allowed:
        db.flush()
    else:
        with pytest.raises(IntegrityError):
            db.flush()


def test_database_rejects_unknown_status(db: Session, company: Company, employee: Employee) -> None:
    """Aunque alguien escriba SQL a mano, no puede guardar un estado inexistente."""
    with pytest.raises(IntegrityError):
        db.execute(
            text(
                "INSERT INTO expenses (id, company_id, employee_id, status, currency, "
                "location_source, data_source, is_reimbursable, version) "
                "VALUES (gen_random_uuid(), :c, :e, 'pagado', 'COP', 'none', 'manual', false, 1)"
            ),
            {"c": company.id, "e": employee.id},
        )


def test_same_image_cannot_be_stored_twice(db: Session, company: Company) -> None:
    def photo(key: str) -> Document:
        return Document(
            company_id=company.id,
            kind=DocumentKind.INVOICE_PHOTO,
            storage_key=key,
            mime_type="image/jpeg",
            size_bytes=1024,
            sha256="f" * 64,
        )

    db.add(photo("2026/11/a.jpg"))
    db.flush()

    db.add(photo("2026/11/b.jpg"))
    with pytest.raises(IntegrityError):
        db.flush()
