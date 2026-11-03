"""Gestión de empleados y de sus códigos de registro desde el panel."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import EDITORS, READERS, client_ip, require_roles
from app.db.session import get_db
from app.models import AdminUser, Employee
from app.models.enums import EmployeeStatus
from app.schemas.employees import (
    EmployeeCreate,
    EmployeeList,
    EmployeeOut,
    EmployeeUpdate,
    IssuedCodeOut,
    PendingCodeOut,
)
from app.services import employees as service
from app.services import registration
from app.services.audit import Actor

router = APIRouter(prefix="/employees", tags=["empleados"])


def _to_out(db: Session, employee: Employee) -> EmployeeOut:
    code = registration.active_code(db, employee)
    pending = None
    if code is not None:
        pending = PendingCodeOut(
            hint=code.code_hint,
            expires_at=code.expires_at,
            expired=code.expires_at <= datetime.now(UTC),
        )
    return EmployeeOut(
        id=employee.id,
        first_name=employee.first_name,
        last_name=employee.last_name,
        internal_id=employee.internal_id,
        status=employee.status,
        telegram_linked=employee.telegram_user_id is not None,
        telegram_username=employee.telegram_username,
        linked_at=employee.linked_at,
        pending_code=pending,
        created_at=employee.created_at,
    )


def _load(db: Session, admin: AdminUser, employee_id: uuid.UUID) -> Employee:
    try:
        return service.get_employee(db, admin.company_id, employee_id)
    except service.EmployeeNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Empleado no encontrado")


DUPLICATE = HTTPException(status.HTTP_409_CONFLICT, "Ya existe un empleado con esa identificación interna")


@router.get("", response_model=EmployeeList)
def list_employees(
    status_filter: EmployeeStatus | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> EmployeeList:
    items, total = service.list_employees(
        db, admin.company_id, status=status_filter, search=search, limit=limit, offset=offset
    )
    return EmployeeList(items=[_to_out(db, e) for e in items], total=total)


@router.post("", response_model=EmployeeOut, status_code=status.HTTP_201_CREATED)
def create_employee(
    body: EmployeeCreate,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    try:
        employee = service.create_employee(
            db, admin.company_id, body.model_dump(), Actor.from_admin(admin, client_ip(request))
        )
    except service.DuplicateInternalIdError:
        raise DUPLICATE
    db.commit()
    return _to_out(db, employee)


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(
    employee_id: uuid.UUID,
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    return _to_out(db, _load(db, admin, employee_id))


@router.patch("/{employee_id}", response_model=EmployeeOut)
def update_employee(
    employee_id: uuid.UUID,
    body: EmployeeUpdate,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    employee = _load(db, admin, employee_id)
    try:
        service.update_employee(
            db,
            employee,
            body.model_dump(exclude_unset=True, exclude_none=True),
            Actor.from_admin(admin, client_ip(request)),
        )
    except service.DuplicateInternalIdError:
        raise DUPLICATE
    db.commit()
    return _to_out(db, employee)


@router.delete("/{employee_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_employee(
    employee_id: uuid.UUID,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> None:
    employee = _load(db, admin, employee_id)
    try:
        service.delete_employee(db, employee, Actor.from_admin(admin, client_ip(request)))
    except service.EmployeeHasRecordsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El empleado tiene gastos, viáticos o documentos registrados. "
            "Por conservación contable no se puede eliminar: desactívalo.",
        )
    db.commit()


# --- Códigos de registro y Telegram ---


@router.post(
    "/{employee_id}/registration-code",
    response_model=IssuedCodeOut,
    status_code=status.HTTP_201_CREATED,
)
def generate_registration_code(
    employee_id: uuid.UUID,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> IssuedCodeOut:
    employee = _load(db, admin, employee_id)
    try:
        issued = registration.generate_code(db, employee, Actor.from_admin(admin, client_ip(request)))
    except registration.EmployeeNotEligibleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return IssuedCodeOut(code=issued.code, hint=issued.hint, expires_at=issued.expires_at)


@router.delete("/{employee_id}/registration-code", status_code=status.HTTP_204_NO_CONTENT)
def revoke_registration_code(
    employee_id: uuid.UUID,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> None:
    employee = _load(db, admin, employee_id)
    if not registration.revoke_active_code(db, employee, Actor.from_admin(admin, client_ip(request))):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El empleado no tiene un código pendiente")
    db.commit()


@router.post("/{employee_id}/unlink-telegram", response_model=EmployeeOut)
def unlink_telegram(
    employee_id: uuid.UUID,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> EmployeeOut:
    employee = _load(db, admin, employee_id)
    if not registration.unlink_telegram(db, employee, Actor.from_admin(admin, client_ip(request))):
        raise HTTPException(status.HTTP_409_CONFLICT, "El empleado no tiene Telegram vinculado")
    db.commit()
    return _to_out(db, employee)
