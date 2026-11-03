"""Reglas de negocio de los empleados."""

import uuid
from typing import Any

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.models import Document, Employee, Expense, Viatico
from app.models.enums import EmployeeStatus
from app.services import audit, registration
from app.services.audit import Actor

ENTITY = "employee"


class EmployeeNotFoundError(Exception):
    pass


class DuplicateInternalIdError(Exception):
    pass


class EmployeeHasRecordsError(Exception):
    """Tiene gastos, viáticos o documentos: no se puede borrar, solo desactivar."""


def _internal_id_taken(
    db: Session, company_id: uuid.UUID, internal_id: str, exclude_id: uuid.UUID | None = None
) -> bool:
    query = select(Employee.id).where(
        Employee.company_id == company_id, Employee.internal_id == internal_id
    )
    if exclude_id is not None:
        query = query.where(Employee.id != exclude_id)
    return db.scalar(select(exists(query))) or False


def get_employee(db: Session, company_id: uuid.UUID, employee_id: uuid.UUID) -> Employee:
    employee = db.get(Employee, employee_id)
    # Otra empresa o borrado = "no existe" (no revelamos que existe en otro lado).
    if employee is None or employee.company_id != company_id or employee.deleted_at is not None:
        raise EmployeeNotFoundError
    return employee


def list_employees(
    db: Session,
    company_id: uuid.UUID,
    *,
    status: EmployeeStatus | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Employee], int]:
    query = select(Employee).where(Employee.company_id == company_id, Employee.deleted_at.is_(None))
    if status is not None:
        query = query.where(Employee.status == status)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(
            or_(
                Employee.first_name.ilike(pattern),
                Employee.last_name.ilike(pattern),
                Employee.internal_id.ilike(pattern),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(
        query.order_by(Employee.last_name, Employee.first_name).limit(limit).offset(offset)
    ).all()
    return list(items), total


def create_employee(
    db: Session, company_id: uuid.UUID, data: dict[str, Any], actor: Actor
) -> Employee:
    if _internal_id_taken(db, company_id, data["internal_id"]):
        raise DuplicateInternalIdError
    employee = Employee(company_id=company_id, **data)
    db.add(employee)
    db.flush()
    audit.log(
        db,
        company_id=company_id,
        entity_type=ENTITY,
        entity_id=employee.id,
        action="create",
        actor=actor,
        new_value=data,
    )
    return employee


def update_employee(
    db: Session, employee: Employee, changes: dict[str, Any], actor: Actor
) -> dict[str, tuple[Any, Any]]:
    new_internal_id = changes.get("internal_id")
    if new_internal_id and _internal_id_taken(
        db, employee.company_id, new_internal_id, exclude_id=employee.id
    ):
        raise DuplicateInternalIdError

    changed = audit.apply_changes(
        db, employee, changes, company_id=employee.company_id, entity_type=ENTITY, actor=actor
    )
    # Un empleado desactivado no debe poder vincularse con un código pendiente.
    if "status" in changed and employee.status == EmployeeStatus.INACTIVE:
        registration.revoke_active_code(db, employee, actor)
    db.flush()
    return changed


def has_financial_records(db: Session, employee: Employee) -> bool:
    return any(
        db.scalar(select(exists().where(model.employee_id == employee.id)))
        for model in (Expense, Viatico, Document)
    )


def delete_employee(db: Session, employee: Employee, actor: Actor) -> None:
    """Borra solo si no tiene registros financieros (soportes contables que deben conservarse)."""
    if has_financial_records(db, employee):
        raise EmployeeHasRecordsError
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type=ENTITY,
        entity_id=employee.id,
        action="delete",
        actor=actor,
        old_value={"internal_id": employee.internal_id},
    )
    db.delete(employee)
    db.flush()
