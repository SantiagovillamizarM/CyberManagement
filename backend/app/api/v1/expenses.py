"""Gastos (facturas) desde el panel: registrar, filtrar, editar y aprobar."""

import uuid
from collections.abc import Callable
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from app.api.deps import EDITORS, READERS, client_ip, require_roles
from app.db.session import get_db
from app.models import AdminUser, AuditLog, Category, Employee, Expense, Supplier
from app.models.enums import ActorType, ExpenseStatus
from app.schemas.common import EmployeeRef
from app.schemas.expenses import (
    CategoryOut,
    ExpenseCreate,
    ExpenseItemOut,
    ExpenseList,
    ExpenseOut,
    ExpenseUpdate,
    HistoryEntry,
    RejectRequest,
    SupplierOut,
)
from app.services import expenses as service
from app.services.audit import Actor

router = APIRouter(prefix="/expenses", tags=["gastos"])

SUPPLIER_FIELDS = {"supplier_nit", "supplier_name"}


def _to_out(db: Session, expense: Expense) -> ExpenseOut:
    employee = db.get(Employee, expense.employee_id)
    category = db.get(Category, expense.category_id) if expense.category_id else None
    supplier = db.get(Supplier, expense.supplier_id) if expense.supplier_id else None
    return ExpenseOut(
        **{field: getattr(expense, field) for field in ExpenseOut.model_fields
           if field not in {"employee", "category", "supplier", "items", "warnings"}},
        employee=EmployeeRef.model_validate(employee, from_attributes=True),
        category=CategoryOut.model_validate(category, from_attributes=True) if category else None,
        supplier=SupplierOut.model_validate(supplier, from_attributes=True) if supplier else None,
        items=[ExpenseItemOut.model_validate(i, from_attributes=True) for i in expense.items],
        warnings=service.warnings(expense),
    )


def _load(db: Session, admin: AdminUser, expense_id: uuid.UUID) -> Expense:
    try:
        return service.get_expense(db, admin.company_id, expense_id)
    except service.ExpenseNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gasto no encontrado")


def _run(action: Callable[[], object]) -> None:
    """Traduce los errores del servicio a respuestas HTTP con mensajes claros."""
    try:
        action()
    except service.ExpenseRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    except service.ExpenseLockedError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    except service.InvalidTransitionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except service.VersionConflictError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Otra persona modificó este gasto mientras lo editabas. Recarga y vuelve a intentar.",
        )
    except service.DuplicateInvoiceError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": exc.reason, "existing_expense_id": str(exc.existing_id)},
        )


@router.get("", response_model=ExpenseList)
def list_expenses(
    employee_id: uuid.UUID | None = None,
    # Se puede repetir: ?status=submitted&status=in_review ("por revisar").
    status_filter: list[ExpenseStatus] | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=200,
                               description="Descripción, establecimiento, NIT o n.º de factura"),
    issue_date_from: date | None = None,
    issue_date_to: date | None = None,
    supplier_nit: str | None = Query(default=None, max_length=20),
    establishment: str | None = Query(default=None, max_length=200),
    min_total: Decimal | None = Query(default=None, ge=0),
    max_total: Decimal | None = Query(default=None, ge=0),
    invoice_number: str | None = Query(default=None, max_length=50),
    category_id: uuid.UUID | None = None,
    viatico_id: uuid.UUID | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> ExpenseList:
    filters = service.ExpenseFilters(
        employee_id=employee_id,
        statuses=tuple(status_filter or ()),
        search=search,
        issue_date_from=issue_date_from,
        issue_date_to=issue_date_to,
        supplier_nit=supplier_nit,
        establishment=establishment,
        min_total=min_total,
        max_total=max_total,
        invoice_number=invoice_number,
        category_id=category_id,
        viatico_id=viatico_id,
    )
    items, total = service.list_expenses(db, admin.company_id, filters, limit=limit, offset=offset)
    return ExpenseList(items=[_to_out(db, e) for e in items], total=total)


@router.post("", response_model=ExpenseOut, status_code=status.HTTP_201_CREATED)
def create_expense(
    body: ExpenseCreate,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> ExpenseOut:
    created: list[Expense] = []

    def action():
        employee = service.employee_of(db, admin.company_id, body.employee_id)
        created.append(
            service.create_expense(
                db,
                employee,
                body.model_dump(exclude={"employee_id", "items", *SUPPLIER_FIELDS}, exclude_none=True),
                items=[i.model_dump() for i in body.items],
                supplier_nit=body.supplier_nit,
                supplier_name=body.supplier_name,
                actor=Actor.from_admin(admin, client_ip(request)),
            )
        )

    _run(action)
    db.commit()
    return _to_out(db, created[0])


@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(
    expense_id: uuid.UUID,
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> ExpenseOut:
    return _to_out(db, _load(db, admin, expense_id))


@router.get("/{expense_id}/history", response_model=list[HistoryEntry])
def get_history(
    expense_id: uuid.UUID,
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> list[HistoryEntry]:
    """Quién cambió qué y cuándo (incluye lo que corrigió el empleado desde el bot)."""
    expense = _load(db, admin, expense_id)
    names: dict[tuple[ActorType, str | None], str] = {}

    def actor_name(entry: AuditLog) -> str:
        key = (entry.actor_type, entry.actor_id)
        if key not in names:
            names[key] = "Sistema"
            if entry.actor_type == ActorType.ADMIN and entry.actor_id:
                person = db.get(AdminUser, uuid.UUID(entry.actor_id))
                names[key] = person.full_name if person else "Administrador"
            elif entry.actor_type == ActorType.EMPLOYEE and entry.actor_id:
                person = db.get(Employee, uuid.UUID(entry.actor_id))
                names[key] = f"{person.first_name} {person.last_name}" if person else "Empleado"
        return names[key]

    return [
        HistoryEntry(entity_type=e.entity_type, action=e.action, field=e.field, old_value=e.old_value,
                     new_value=e.new_value, reason=e.reason, actor_type=e.actor_type, actor_name=actor_name(e),
                     created_at=e.created_at)
        for e in service.history(db, expense)
    ]


@router.patch("/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: uuid.UUID,
    body: ExpenseUpdate,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> ExpenseOut:
    expense = _load(db, admin, expense_id)
    sent = body.model_fields_set  # solo lo que el cliente envió (incluye nulls explícitos)
    changes = body.model_dump(include=sent - {"version", "items", *SUPPLIER_FIELDS})
    supplier_kwargs = {}
    if "supplier_nit" in sent:
        supplier_kwargs = {"supplier_nit": body.supplier_nit, "supplier_name": body.supplier_name}

    _run(
        lambda: service.update_expense(
            db,
            expense,
            changes,
            version=body.version,
            actor=Actor.from_admin(admin, client_ip(request)),
            items=[i.model_dump() for i in body.items] if body.items is not None else None,
            **supplier_kwargs,
        )
    )
    db.commit()
    return _to_out(db, expense)


def _status_action(name: str):
    def endpoint(
        expense_id: uuid.UUID,
        request: Request,
        admin: AdminUser = Depends(require_roles(*EDITORS)),
        db: Session = Depends(get_db),
    ) -> ExpenseOut:
        expense = _load(db, admin, expense_id)
        action = getattr(service, name)
        _run(lambda: action(db, expense, Actor.from_admin(admin, client_ip(request))))
        db.commit()
        return _to_out(db, expense)

    endpoint.__name__ = f"{name}_expense"
    return endpoint


for _path, _name in (
    ("submit", "submit"),
    ("start-review", "start_review"),
    ("approve", "approve"),
    ("close", "close"),
):
    router.add_api_route(
        f"/{{expense_id}}/{_path}", _status_action(_name), methods=["POST"], response_model=ExpenseOut
    )


@router.post("/{expense_id}/reject", response_model=ExpenseOut)
def reject_expense(
    expense_id: uuid.UUID,
    body: RejectRequest,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> ExpenseOut:
    expense = _load(db, admin, expense_id)
    _run(lambda: service.reject(db, expense, Actor.from_admin(admin, client_ip(request)), body.reason))
    db.commit()
    return _to_out(db, expense)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(
    expense_id: uuid.UUID,
    request: Request,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
) -> None:
    expense = _load(db, admin, expense_id)
    _run(lambda: service.delete_expense(db, expense, Actor.from_admin(admin, client_ip(request))))
    db.commit()
