"""Viáticos desde el panel: abrir, registrar movimientos, consultar saldo y cerrar."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import EDITORS, READERS, client_ip, require_roles
from app.db.session import get_db
from app.models import AdminUser, Employee, Viatico
from app.models.enums import MovementSource, ViaticoStatus
from app.schemas.common import EmployeeRef
from app.schemas.viaticos import BalanceOut, MovementCreate, MovementOut, ViaticoCreate, ViaticoOut
from app.services import employees as employee_service
from app.services import viaticos as service
from app.services.audit import Actor

router = APIRouter(prefix="/viaticos", tags=["viáticos"])


def _movement_out(db: Session, movement, names: dict) -> MovementOut:
    if movement.created_by_id and movement.created_by_id not in names:
        admin = db.get(AdminUser, movement.created_by_id)
        names[movement.created_by_id] = admin.full_name if admin else "Administrador"
    out = MovementOut.model_validate(movement, from_attributes=True)
    out.created_by_name = names.get(movement.created_by_id)
    return out


def _to_out(db: Session, viatico: Viatico) -> ViaticoOut:
    employee = db.get(Employee, viatico.employee_id)
    balance = service.compute_balance(db, viatico)
    names: dict = {}
    return ViaticoOut(
        id=viatico.id,
        employee=EmployeeRef.model_validate(employee, from_attributes=True),
        purpose=viatico.purpose,
        status=viatico.status,
        opened_at=viatico.opened_at,
        closed_at=viatico.closed_at,
        balance=BalanceOut(**balance.__dict__),
        movements=[_movement_out(db, m, names) for m in viatico.movements],
        unresolved_expenses=service.count_unresolved(db, viatico),
    )


def _load(db: Session, admin: AdminUser, viatico_id: uuid.UUID) -> Viatico:
    try:
        return service.get_viatico(db, admin.company_id, viatico_id)
    except service.ViaticoNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Viático no encontrado")


CLOSED = HTTPException(status.HTTP_409_CONFLICT, "El viático está cerrado")


@router.get("", response_model=list[ViaticoOut])
def list_viaticos(
    employee_id: uuid.UUID | None = None,
    status_filter: ViaticoStatus | None = Query(default=None, alias="status"),
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
):
    viaticos = service.list_viaticos(
        db, admin.company_id, employee_id=employee_id, status=status_filter
    )
    return [_to_out(db, v) for v in viaticos]


@router.post("", response_model=ViaticoOut, status_code=status.HTTP_201_CREATED)
def open_viatico(
    body: ViaticoCreate,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
):
    try:
        employee = employee_service.get_employee(db, admin.company_id, body.employee_id)
        viatico = service.open_viatico(
            db,
            employee,
            initial_amount=body.initial_amount,
            purpose=body.purpose,
            source=MovementSource.ADMIN,
            actor=Actor.from_admin(admin, client_ip(request)),
        )
    except employee_service.EmployeeNotFoundError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El empleado no existe")
    except service.ViaticoRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    db.commit()
    return _to_out(db, viatico)


@router.get("/{viatico_id}", response_model=ViaticoOut)
def get_viatico(
    viatico_id: uuid.UUID,
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
):
    return _to_out(db, _load(db, admin, viatico_id))


@router.post("/{viatico_id}/movements", response_model=ViaticoOut, status_code=status.HTTP_201_CREATED)
def add_movement(
    viatico_id: uuid.UUID,
    body: MovementCreate,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
):
    viatico = _load(db, admin, viatico_id)
    try:
        service.add_movement(
            db,
            viatico,
            type=body.type,
            amount=body.amount,
            note=body.note,
            source=MovementSource.ADMIN,
            actor=Actor.from_admin(admin, client_ip(request)),
        )
    except service.ViaticoClosedError:
        raise CLOSED
    except service.ViaticoRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    db.commit()
    db.refresh(viatico)
    return _to_out(db, viatico)


@router.post("/{viatico_id}/close", response_model=ViaticoOut)
def close_viatico(
    viatico_id: uuid.UUID,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
):
    viatico = _load(db, admin, viatico_id)
    try:
        service.close_viatico(db, viatico, Actor.from_admin(admin, client_ip(request)))
    except service.ViaticoClosedError:
        raise CLOSED
    except service.ViaticoRuleError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    db.commit()
    return _to_out(db, viatico)
