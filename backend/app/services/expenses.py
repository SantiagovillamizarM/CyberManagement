"""Gastos: registro, edición con historial, ciclo de aprobación y filtros.

Ciclo de vida:
    draft / pending_completion ──submit──► submitted ──start_review──► in_review
    rejected ──submit──► submitted          submitted / in_review ──approve──► approved ──close──► closed
                                            submitted / in_review ──reject───► rejected
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.models import Employee, Expense, ExpenseItem, Supplier, Viatico
from app.models.enums import (
    ActorType,
    DataSource,
    EmployeeStatus,
    ExpenseStatus,
    LocationSource,
    PaymentSource,
    ViaticoStatus,
)
from app.services import audit, catalog, notifications
from app.services.audit import Actor
from app.utils.dates import local_today
from app.utils.nit import parse_nit

ENTITY = "expense"
CENT = Decimal("0.01")
COORD = Decimal("0.000001")
TOLERANCE = Decimal("1.00")  # diferencias de redondeo de hasta 1 peso no generan aviso

# Estados en los que el EMPLEADO ya no puede editar (el administrador sí).
EMPLOYEE_LOCKED = {ExpenseStatus.APPROVED, ExpenseStatus.CLOSED}
SUBMITTABLE_FROM = {ExpenseStatus.DRAFT, ExpenseStatus.PENDING_COMPLETION, ExpenseStatus.REJECTED}
REVIEWABLE = {ExpenseStatus.SUBMITTED, ExpenseStatus.IN_REVIEW}
NOT_DELETABLE = {ExpenseStatus.APPROVED, ExpenseStatus.CLOSED}

EDITABLE_FIELDS = {
    "description",
    "invoice_number",
    "cufe",
    "issue_date",
    "issue_time",
    "subtotal",
    "tax_total",
    "total",
    "category_id",
    "viatico_id",
    "payment_source",
    "location_lat",
    "location_lon",
    "location_text",
}
LOCATION_FIELDS = {"location_lat", "location_lon", "location_text"}


# --- Errores (cada uno se traduce a un código HTTP en la API) ---


class ExpenseNotFoundError(Exception):
    pass


class ExpenseRuleError(ValueError):
    """Dato inválido según las reglas de negocio (422). El mensaje es para el usuario."""


class ExpenseLockedError(Exception):
    """El actor no puede modificar el gasto en su estado actual (403)."""


class InvalidTransitionError(Exception):
    """Cambio de estado no permitido (409)."""


class VersionConflictError(Exception):
    """Otra persona modificó el gasto mientras tanto (409)."""


class DuplicateInvoiceError(Exception):
    def __init__(self, existing_id: uuid.UUID, reason: str) -> None:
        super().__init__(reason)
        self.existing_id = existing_id
        self.reason = reason


# --- Consultas ---


@dataclass
class ExpenseFilters:
    employee_id: uuid.UUID | None = None
    status: ExpenseStatus | None = None
    issue_date_from: date | None = None
    issue_date_to: date | None = None
    supplier_nit: str | None = None
    establishment: str | None = None
    min_total: Decimal | None = None
    max_total: Decimal | None = None
    invoice_number: str | None = None
    category_id: uuid.UUID | None = None
    viatico_id: uuid.UUID | None = None


def get_expense(db: Session, company_id: uuid.UUID, expense_id: uuid.UUID) -> Expense:
    expense = db.get(Expense, expense_id)
    if expense is None or expense.company_id != company_id or expense.deleted_at is not None:
        raise ExpenseNotFoundError
    return expense


def list_expenses(
    db: Session, company_id: uuid.UUID, filters: ExpenseFilters, *, limit: int = 50, offset: int = 0
) -> tuple[list[Expense], int]:
    query = (
        select(Expense)
        .outerjoin(Supplier, Supplier.id == Expense.supplier_id)
        .where(Expense.company_id == company_id, Expense.deleted_at.is_(None))
    )
    f = filters
    if f.employee_id:
        query = query.where(Expense.employee_id == f.employee_id)
    if f.status:
        query = query.where(Expense.status == f.status)
    if f.issue_date_from:
        query = query.where(Expense.issue_date >= f.issue_date_from)
    if f.issue_date_to:
        query = query.where(Expense.issue_date <= f.issue_date_to)
    if f.supplier_nit:
        digits = "".join(c for c in f.supplier_nit.split("-")[0] if c.isdigit())
        query = query.where(Supplier.nit == digits)
    if f.establishment:
        query = query.where(Supplier.name.ilike(f"%{f.establishment.strip()}%"))
    if f.min_total is not None:
        query = query.where(Expense.total >= f.min_total)
    if f.max_total is not None:
        query = query.where(Expense.total <= f.max_total)
    if f.invoice_number:
        query = query.where(Expense.invoice_number.ilike(f"%{f.invoice_number.strip()}%"))
    if f.category_id:
        query = query.where(Expense.category_id == f.category_id)
    if f.viatico_id:
        query = query.where(Expense.viatico_id == f.viatico_id)

    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Expense.received_at.desc()).limit(limit).offset(offset)).all()
    return list(items), total


# --- Validaciones ---


def _viatico_is_closed(db: Session, expense: Expense) -> bool:
    if expense.viatico_id is None:
        return False
    viatico = db.get(Viatico, expense.viatico_id)
    return viatico is not None and viatico.status == ViaticoStatus.CLOSED


def _validate(db: Session, expense: Expense, *, viatico_assigned: bool) -> None:
    """`viatico_assigned`: el gasto es nuevo o se está cambiando de viático.

    Un viático cerrado no acepta gastos NUEVOS, pero el administrador sí puede
    corregir los que ya tenía (queda en el historial).
    """
    if expense.issue_date and expense.issue_date > local_today():
        raise ExpenseRuleError("La fecha de expedición no puede ser futura")

    if (expense.location_lat is None) != (expense.location_lon is None):
        raise ExpenseRuleError("La ubicación necesita latitud y longitud juntas")

    if expense.category_id is not None:
        try:
            catalog.get_category(db, expense.company_id, expense.category_id)
        except catalog.CategoryNotFoundError:
            raise ExpenseRuleError("La categoría no existe")

    if expense.viatico_id is not None:
        viatico = db.get(Viatico, expense.viatico_id)
        if viatico is None or viatico.employee_id != expense.employee_id:
            raise ExpenseRuleError("El viático no pertenece a este empleado")
        if viatico_assigned and viatico.status == ViaticoStatus.CLOSED:
            raise ExpenseRuleError("El viático está cerrado")
        if expense.payment_source is None:
            expense.payment_source = PaymentSource.VIATICO
        elif expense.payment_source != PaymentSource.VIATICO:
            raise ExpenseRuleError("Un gasto asociado a un viático debe pagarse con el viático")
    elif expense.payment_source == PaymentSource.VIATICO:
        raise ExpenseRuleError("Indica a qué viático corresponde el gasto")

    # Pagó con su propio dinero: la empresa se lo debe reembolsar.
    expense.is_reimbursable = expense.payment_source == PaymentSource.OWN_MONEY


def _check_duplicates(db: Session, expense: Expense) -> None:
    base = select(Expense.id).where(
        Expense.company_id == expense.company_id,
        Expense.deleted_at.is_(None),
        Expense.id != expense.id,
    )
    if expense.cufe:
        existing = db.scalar(base.where(Expense.cufe == expense.cufe))
        if existing:
            raise DuplicateInvoiceError(existing, "Ya existe un gasto con ese CUFE")
    if expense.supplier_id and expense.invoice_number:
        existing = db.scalar(
            base.where(
                Expense.supplier_id == expense.supplier_id,
                Expense.invoice_number == expense.invoice_number,
            )
        )
        if existing:
            raise DuplicateInvoiceError(
                existing, "Ya existe un gasto con ese número de factura para el mismo NIT"
            )


def _build_items(items: list[dict[str, Any]]) -> list[ExpenseItem]:
    built = []
    for position, item in enumerate(items):
        line_total = item.get("line_total")
        if line_total is None and item.get("unit_price") is not None:
            line_total = (item["unit_price"] * item.get("quantity", 1)).quantize(CENT)
        built.append(
            ExpenseItem(
                position=position,
                description=item["description"],
                quantity=item.get("quantity", Decimal("1")),
                unit_price=item.get("unit_price"),
                line_total=line_total,
            )
        )
    return built


def _items_snapshot(expense: Expense) -> list[dict[str, Any]]:
    return [
        {
            "description": i.description,
            "quantity": str(i.quantity),
            "unit_price": str(i.unit_price) if i.unit_price is not None else None,
            "line_total": str(i.line_total) if i.line_total is not None else None,
        }
        for i in expense.items
    ]


MONEY_FIELDS = ("subtotal", "tax_total", "total")


def _quantize_money(changes: dict[str, Any]) -> None:
    for field in MONEY_FIELDS:
        if changes.get(field) is not None:
            changes[field] = Decimal(changes[field]).quantize(CENT)


def _quantize_location(changes: dict[str, Any]) -> None:
    for field in ("location_lat", "location_lon"):
        if changes.get(field) is not None:
            changes[field] = Decimal(changes[field]).quantize(COORD)


def _location_source(expense: Expense) -> LocationSource:
    if expense.location_lat is None and not expense.location_text:
        return LocationSource.NONE  # "No disponible": nunca se inventa
    return LocationSource.MANUAL


def warnings(expense: Expense) -> list[str]:
    """Avisos de coherencia. NO bloquean: las facturas reales a veces tienen propinas o descuentos."""
    found = []
    if None not in (expense.subtotal, expense.tax_total, expense.total):
        if abs(expense.subtotal + expense.tax_total - expense.total) > TOLERANCE:
            found.append("El subtotal más los impuestos no coincide con el total")
    line_totals = [i.line_total for i in expense.items if i.line_total is not None]
    reference = expense.subtotal if expense.subtotal is not None else expense.total
    if line_totals and reference is not None and len(line_totals) == len(expense.items):
        if abs(sum(line_totals, Decimal("0")) - reference) > TOLERANCE:
            found.append("La suma de los productos no coincide con el subtotal")
    if expense.supplier_id is None:
        found.append("No se registró el NIT del establecimiento")
    return found


# --- Creación y edición ---


def employee_can_edit(db: Session, expense: Expense) -> bool:
    """¿El empleado dueño puede corregir este gasto? (misma regla que aplica _assert_can_edit)."""
    return expense.status not in EMPLOYEE_LOCKED and not _viatico_is_closed(db, expense)


def _assert_can_edit(db: Session, expense: Expense, actor: Actor) -> None:
    if actor.type == ActorType.EMPLOYEE:
        if actor.id != str(expense.employee_id):
            raise ExpenseLockedError("Solo puedes modificar tus propios gastos")
        if expense.status in EMPLOYEE_LOCKED:
            raise ExpenseLockedError("El gasto ya fue aprobado o cerrado; no se puede modificar")
        if _viatico_is_closed(db, expense):
            raise ExpenseLockedError("El viático de este gasto ya fue cerrado")


def _resolve_supplier(db: Session, company_id: uuid.UUID, nit: str | None, name: str | None):
    if not nit:
        return None
    try:
        return catalog.get_or_create_supplier(db, company_id, nit, name)
    except ValueError as exc:  # InvalidNitError
        raise ExpenseRuleError(str(exc))


def create_expense(
    db: Session,
    employee: Employee,
    data: dict[str, Any],
    *,
    items: list[dict[str, Any]] | None = None,
    supplier_nit: str | None = None,
    supplier_name: str | None = None,
    actor: Actor,
    data_source: DataSource = DataSource.MANUAL,
    location_source: LocationSource | None = None,
) -> Expense:
    """`location_source`: de dónde vienen las coordenadas (ej. TELEGRAM si el empleado
    compartió su ubicación). Si se omite y hay coordenadas, se asume MANUAL."""
    if employee.status != EmployeeStatus.ACTIVE or employee.deleted_at is not None:
        raise ExpenseRuleError("El empleado está desactivado")

    data = {k: v for k, v in data.items() if k in EDITABLE_FIELDS}
    _quantize_money(data)
    _quantize_location(data)
    expense = Expense(
        id=uuid.uuid4(),
        company_id=employee.company_id,
        employee_id=employee.id,
        data_source=data_source,
        **data,
    )
    supplier = _resolve_supplier(db, employee.company_id, supplier_nit, supplier_name)
    expense.supplier_id = supplier.id if supplier else None
    expense.items = _build_items(items or [])
    expense.location_source = _location_source(expense)
    if location_source is not None and expense.location_source != LocationSource.NONE:
        expense.location_source = location_source

    _validate(db, expense, viatico_assigned=True)
    _check_duplicates(db, expense)
    db.add(expense)
    db.flush()
    audit.log(
        db,
        company_id=expense.company_id,
        entity_type=ENTITY,
        entity_id=expense.id,
        action="create",
        actor=actor,
        new_value={**data, "supplier_nit": supplier_nit, "items": _items_snapshot(expense)},
    )
    return expense


_UNSET: Any = object()


def update_expense(
    db: Session,
    expense: Expense,
    changes: dict[str, Any],
    *,
    version: int,
    actor: Actor,
    items: list[dict[str, Any]] | None = None,
    supplier_nit: str | None = _UNSET,
    supplier_name: str | None = None,
) -> Expense:
    """Aplica cambios parciales. `supplier_nit=None` quita el proveedor; omitirlo no lo toca."""
    _assert_can_edit(db, expense, actor)
    if version != expense.version:
        raise VersionConflictError
    # - Sin autoflush: las consultas de validación (duplicados, categoría...) no deben enviar
    #   los cambios a la BD antes de validarlos; si no, la BD rechazaría un duplicado con
    #   un error 500 en vez de que lo detectemos nosotros y respondamos 409.
    # - Punto de guardado: si la edición falla, el gasto vuelve a como estaba (no queda
    #   "a medio modificar" en memoria para quien siga usando la sesión).
    with db.no_autoflush, db.begin_nested():
        return _update_expense(db, expense, changes, actor=actor, items=items,
                               supplier_nit=supplier_nit, supplier_name=supplier_name)


def _update_expense(
    db: Session,
    expense: Expense,
    changes: dict[str, Any],
    *,
    actor: Actor,
    items: list[dict[str, Any]] | None,
    supplier_nit: Any,
    supplier_name: str | None,
) -> Expense:
    changes = {k: v for k, v in changes.items() if k in EDITABLE_FIELDS}
    _quantize_money(changes)
    _quantize_location(changes)
    changed = audit.apply_changes(
        db, expense, changes, company_id=expense.company_id, entity_type=ENTITY, actor=actor
    )

    if supplier_nit is not _UNSET:
        old_supplier = db.get(Supplier, expense.supplier_id) if expense.supplier_id else None
        new_supplier = _resolve_supplier(db, expense.company_id, supplier_nit, supplier_name)
        if (old_supplier and old_supplier.id) != (new_supplier and new_supplier.id):
            expense.supplier_id = new_supplier.id if new_supplier else None
            changed["supplier"] = (old_supplier, new_supplier)
            audit.log(
                db,
                company_id=expense.company_id,
                entity_type=ENTITY,
                entity_id=expense.id,
                action="update",
                actor=actor,
                field="supplier_nit",
                old_value=old_supplier.nit if old_supplier else None,
                new_value=new_supplier.nit if new_supplier else None,
            )

    if items is not None:
        before = _items_snapshot(expense)
        expense.items = _build_items(items)
        after = _items_snapshot(expense)
        if before != after:
            changed["items"] = (before, after)
            audit.log(
                db,
                company_id=expense.company_id,
                entity_type=ENTITY,
                entity_id=expense.id,
                action="update",
                actor=actor,
                field="items",
                old_value=before,
                new_value=after,
            )

    if not changed:
        return expense

    if LOCATION_FIELDS & changed.keys():
        expense.location_source = _location_source(expense)
    if expense.data_source == DataSource.AI:
        expense.data_source = DataSource.MIXED  # la IA lo extrajo y una persona lo corrigió
    _validate(db, expense, viatico_assigned="viatico_id" in changed)
    _check_duplicates(db, expense)
    # Fuerza el UPDATE de la fila (y que suba la versión) aunque solo cambien las líneas.
    expense.updated_at = datetime.now(UTC)
    try:
        db.flush()
    except StaleDataError:
        raise VersionConflictError
    return expense


# --- Ciclo de aprobación ---


def _transition(
    db: Session,
    expense: Expense,
    *,
    allowed_from: set[ExpenseStatus],
    to: ExpenseStatus,
    actor: Actor,
    reason: str | None = None,
) -> Expense:
    if expense.status not in allowed_from:
        raise InvalidTransitionError(
            f"No se puede pasar de '{expense.status.value}' a '{to.value}'"
        )
    old = expense.status
    expense.status = to
    audit.log(
        db,
        company_id=expense.company_id,
        entity_type=ENTITY,
        entity_id=expense.id,
        action=f"status_{to.value}",
        actor=actor,
        field="status",
        old_value=old,
        new_value=to,
        reason=reason,
    )
    try:
        db.flush()
    except StaleDataError:
        raise VersionConflictError
    return expense


def missing_for_submit(expense: Expense) -> list[str]:
    missing = []
    if expense.total is None or expense.total <= 0:
        missing.append("total")
    if not expense.description:
        missing.append("description")
    if expense.issue_date is None:
        missing.append("issue_date")
    return missing


def submit(db: Session, expense: Expense, actor: Actor) -> Expense:
    _assert_can_edit(db, expense, actor)
    if _viatico_is_closed(db, expense):
        raise ExpenseRuleError("El viático de este gasto ya fue cerrado; no se puede reenviar")
    missing = missing_for_submit(expense)
    if missing:
        raise ExpenseRuleError("Faltan datos obligatorios: " + ", ".join(missing))
    expense.rejection_reason = None
    return _transition(db, expense, allowed_from=SUBMITTABLE_FROM, to=ExpenseStatus.SUBMITTED, actor=actor)


def start_review(db: Session, expense: Expense, actor: Actor) -> Expense:
    return _transition(
        db, expense, allowed_from={ExpenseStatus.SUBMITTED}, to=ExpenseStatus.IN_REVIEW, actor=actor
    )


def approve(db: Session, expense: Expense, actor: Actor) -> Expense:
    expense.reviewed_by_id = actor.admin_id
    expense.reviewed_at = datetime.now(UTC)
    _transition(db, expense, allowed_from=REVIEWABLE, to=ExpenseStatus.APPROVED, actor=actor)
    notifications.expense_reviewed(db, expense)  # misma transacción que la aprobación
    return expense


def reject(db: Session, expense: Expense, actor: Actor, reason: str) -> Expense:
    if not reason or len(reason.strip()) < 5:
        raise ExpenseRuleError("Indica el motivo del rechazo (mínimo 5 caracteres)")
    expense.rejection_reason = reason.strip()
    expense.reviewed_by_id = actor.admin_id
    expense.reviewed_at = datetime.now(UTC)
    _transition(db, expense, allowed_from=REVIEWABLE, to=ExpenseStatus.REJECTED, actor=actor, reason=reason)
    notifications.expense_reviewed(db, expense)
    return expense


def close(db: Session, expense: Expense, actor: Actor) -> Expense:
    return _transition(
        db, expense, allowed_from={ExpenseStatus.APPROVED}, to=ExpenseStatus.CLOSED, actor=actor
    )


def delete_expense(db: Session, expense: Expense, actor: Actor) -> None:
    """Borrado lógico: el registro se conserva para auditoría y contabilidad."""
    _assert_can_edit(db, expense, actor)
    if expense.status in NOT_DELETABLE:
        raise InvalidTransitionError("Un gasto aprobado o cerrado no se puede eliminar")
    expense.deleted_at = datetime.now(UTC)
    audit.log(
        db,
        company_id=expense.company_id,
        entity_type=ENTITY,
        entity_id=expense.id,
        action="delete",
        actor=actor,
    )
    db.flush()


def employee_of(db: Session, company_id: uuid.UUID, employee_id: uuid.UUID) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None or employee.company_id != company_id or employee.deleted_at is not None:
        raise ExpenseRuleError("El empleado no existe")
    return employee
