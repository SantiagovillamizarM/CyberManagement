"""Viáticos: dinero entregado al empleado y su saldo.

Concepto clave: el dinero ENTREGADO (movimientos) es distinto del dinero
GASTADO (facturas). El saldo es la diferencia y nunca se guarda: se calcula.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Employee, Expense, Viatico, ViaticoMovement
from app.models.enums import (
    EmployeeStatus,
    ExpenseStatus,
    MovementSource,
    MovementType,
    ViaticoStatus,
)
from app.services import audit, notifications
from app.services.audit import Actor

ENTITY = "viatico"
ZERO = Decimal("0.00")

# Gastos que cuentan como dinero ya gastado del viático.
COUNTED_STATUSES = (
    ExpenseStatus.SUBMITTED,
    ExpenseStatus.IN_REVIEW,
    ExpenseStatus.APPROVED,
    ExpenseStatus.CLOSED,
)
# Gastos que impiden cerrar el viático (deben resolverse antes).
UNRESOLVED_STATUSES = (
    ExpenseStatus.DRAFT,
    ExpenseStatus.PENDING_COMPLETION,
    ExpenseStatus.SUBMITTED,
    ExpenseStatus.IN_REVIEW,
)


class ViaticoNotFoundError(Exception):
    pass


class ViaticoClosedError(Exception):
    pass


class ViaticoRuleError(ValueError):
    """Regla de negocio incumplida; el mensaje se puede mostrar al usuario."""


class Settlement(StrEnum):
    EMPLOYEE_MUST_RETURN = "employee_must_return"  # le sobró dinero: debe reintegrar
    COMPANY_MUST_REIMBURSE = "company_must_reimburse"  # gastó de más: la empresa le debe
    BALANCED = "balanced"  # a paz y salvo


@dataclass(frozen=True)
class Balance:
    delivered: Decimal  # inicial + adicional ± ajustes
    refunded: Decimal  # devuelto por el empleado
    spent: Decimal  # facturas registradas (no rechazadas ni borradas)
    spent_approved: Decimal  # de lo gastado, lo ya aprobado
    remaining: Decimal  # delivered - refunded - spent
    settlement: Settlement


def _now() -> datetime:
    return datetime.now(UTC)


def get_viatico(db: Session, company_id: uuid.UUID, viatico_id: uuid.UUID) -> Viatico:
    viatico = db.get(Viatico, viatico_id)
    if viatico is None:
        raise ViaticoNotFoundError
    employee = db.get(Employee, viatico.employee_id)
    if employee.company_id != company_id:
        raise ViaticoNotFoundError
    return viatico


def list_viaticos(
    db: Session,
    company_id: uuid.UUID,
    *,
    employee_id: uuid.UUID | None = None,
    status: ViaticoStatus | None = None,
) -> list[Viatico]:
    query = (
        select(Viatico)
        .join(Employee, Employee.id == Viatico.employee_id)
        .where(Employee.company_id == company_id)
    )
    if employee_id is not None:
        query = query.where(Viatico.employee_id == employee_id)
    if status is not None:
        query = query.where(Viatico.status == status)
    return list(db.scalars(query.order_by(Viatico.opened_at.desc())))


def compute_balance(db: Session, viatico: Viatico) -> Balance:
    movements = db.execute(
        select(ViaticoMovement.type, func.coalesce(func.sum(ViaticoMovement.amount), ZERO))
        .where(ViaticoMovement.viatico_id == viatico.id)
        .group_by(ViaticoMovement.type)
    ).all()
    by_type = {MovementType(t): amount for t, amount in movements}
    refunded = by_type.pop(MovementType.REFUND, ZERO)
    delivered = sum(by_type.values(), ZERO)

    def spent_in(statuses) -> Decimal:
        return db.scalar(
            select(func.coalesce(func.sum(Expense.total), ZERO)).where(
                Expense.viatico_id == viatico.id,
                Expense.deleted_at.is_(None),
                Expense.status.in_(statuses),
            )
        )

    spent = spent_in(COUNTED_STATUSES)
    spent_approved = spent_in((ExpenseStatus.APPROVED, ExpenseStatus.CLOSED))
    remaining = delivered - refunded - spent
    if remaining > 0:
        settlement = Settlement.EMPLOYEE_MUST_RETURN
    elif remaining < 0:
        settlement = Settlement.COMPANY_MUST_REIMBURSE
    else:
        settlement = Settlement.BALANCED
    return Balance(delivered, refunded, spent, spent_approved, remaining, settlement)


def open_viatico(
    db: Session,
    employee: Employee,
    *,
    initial_amount: Decimal,
    purpose: str | None,
    source: MovementSource,
    actor: Actor,
) -> Viatico:
    if employee.status != EmployeeStatus.ACTIVE or employee.deleted_at is not None:
        raise ViaticoRuleError("El empleado está desactivado")
    if initial_amount <= 0:
        raise ViaticoRuleError("El dinero inicial debe ser mayor que cero")

    viatico = Viatico(employee_id=employee.id, purpose=purpose)
    viatico.movements = [
        ViaticoMovement(
            type=MovementType.INITIAL,
            amount=initial_amount,
            source=source,
            created_by_id=actor.admin_id,
        )
    ]
    db.add(viatico)
    db.flush()
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type=ENTITY,
        entity_id=viatico.id,
        action="open",
        actor=actor,
        new_value={"initial_amount": initial_amount, "purpose": purpose, "source": source},
    )
    if source == MovementSource.ADMIN:  # si lo declaró el propio empleado en el bot, no hace falta avisarle
        notifications.viatico_opened(db, employee, viatico, initial_amount)
    return viatico


def add_movement(
    db: Session,
    viatico: Viatico,
    *,
    type: MovementType,
    amount: Decimal,
    note: str | None,
    source: MovementSource,
    actor: Actor,
) -> ViaticoMovement:
    if viatico.status == ViaticoStatus.CLOSED:
        raise ViaticoClosedError
    if type == MovementType.INITIAL:
        raise ViaticoRuleError("El dinero inicial solo se registra al abrir el viático")
    if type == MovementType.ADJUSTMENT:
        if amount == 0:
            raise ViaticoRuleError("El ajuste no puede ser cero")
        if not note:
            raise ViaticoRuleError("Un ajuste requiere una nota que lo explique")
    elif amount <= 0:
        raise ViaticoRuleError("El valor debe ser mayor que cero")

    movement = ViaticoMovement(
        viatico_id=viatico.id,
        type=type,
        amount=amount,
        source=source,
        note=note,
        created_by_id=actor.admin_id,
    )
    db.add(movement)
    db.flush()
    employee = db.get(Employee, viatico.employee_id)
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type=ENTITY,
        entity_id=viatico.id,
        action=f"movement_{type.value}",
        actor=actor,
        new_value={"amount": amount, "note": note},
    )
    if source == MovementSource.ADMIN:
        notifications.viatico_movement(db, employee, viatico, type=type, amount=amount, note=note,
                                       remaining=compute_balance(db, viatico).remaining)
    return movement


def count_unresolved(db: Session, viatico: Viatico) -> int:
    """Gastos del viático que aún no se aprueban ni rechazan: impiden cerrarlo."""
    return db.scalar(
        select(func.count()).where(
            Expense.viatico_id == viatico.id,
            Expense.deleted_at.is_(None),
            Expense.status.in_(UNRESOLVED_STATUSES),
        )
    )


def close_viatico(db: Session, viatico: Viatico, actor: Actor) -> Balance:
    if viatico.status == ViaticoStatus.CLOSED:
        raise ViaticoClosedError
    unresolved = count_unresolved(db, viatico)
    if unresolved:
        raise ViaticoRuleError(
            f"Hay {unresolved} gasto(s) sin aprobar o rechazar. Resuélvelos antes de cerrar."
        )

    balance = compute_balance(db, viatico)
    viatico.status = ViaticoStatus.CLOSED
    viatico.closed_at = _now()
    viatico.closed_by_id = actor.admin_id
    employee = db.get(Employee, viatico.employee_id)
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type=ENTITY,
        entity_id=viatico.id,
        action="close",
        actor=actor,
        new_value={"remaining": balance.remaining, "settlement": balance.settlement},
    )
    db.flush()
    notifications.viatico_closed(db, employee, viatico, balance)
    return balance
