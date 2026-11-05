"""Estadísticas del dashboard (paso 7.4).

Todo se agrega EN LA BASE DE DATOS: el número de consultas es fijo y no crece con la
cantidad de gastos ni de viáticos (antes el navegador descargaba hasta 200 gastos y sumaba,
así que con más volumen los totales quedaban incompletos sin que nadie lo notara).

Reglas (las mismas del resto del sistema):
- Cuenta como gastado: enviado, en revisión, aprobado y cerrado. Los borradores, pendientes,
  rechazados y eliminados no.
- El día de un gasto es la fecha de la factura o, si falta, el día de recepción EN COLOMBIA
  (un gasto recibido a las 11:30 p. m. cuenta para ese día, no para el siguiente en UTC).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import Date, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Category, Document, Employee, Expense, Viatico, ViaticoMovement
from app.models.enums import DocumentKind, EmployeeStatus, ExpenseStatus, MovementType, ProcessingStatus, ViaticoStatus
from app.services.viaticos import COUNTED_STATUSES
from app.utils.dates import local_today

ZERO = Decimal("0.00")
DAYS = 30
TOP_EMPLOYEES = 8


@dataclass
class DashboardStats:
    today: date
    month_total: Decimal
    previous_month_total: Decimal
    daily: list[tuple[date, Decimal]]
    by_status: dict[str, tuple[int, Decimal]]
    by_category: list[tuple[str, Decimal]]
    by_employee: list[tuple[uuid.UUID, str, int, Decimal]]
    employees_total: int
    employees_active: int
    employees_linked: int
    viaticos_open: int
    viaticos_delivered: Decimal
    viaticos_spent: Decimal
    viaticos_remaining: Decimal
    viaticos_overspent: int
    unregistered_photos: int
    counted_total: Decimal = ZERO
    counted_count: int = 0


def _expense_day():
    """Fecha de la factura o, si falta, la de recepción en la zona horaria de la empresa."""
    received_local = cast(func.timezone(get_settings().timezone, Expense.received_at), Date)
    return func.coalesce(Expense.issue_date, received_local)


def _first_of_previous_month(day: date) -> date:
    return (day.replace(day=1) - timedelta(days=1)).replace(day=1)


def dashboard(db: Session, company_id: uuid.UUID) -> DashboardStats:
    today = local_today()
    month_start = today.replace(day=1)
    previous_start = _first_of_previous_month(today)
    series_start = today - timedelta(days=DAYS - 1)
    day = _expense_day()  # la expresión; con alias solo donde se agrupa por día

    alive = (Expense.company_id == company_id, Expense.deleted_at.is_(None))
    counted = (*alive, Expense.status.in_(COUNTED_STATUSES), Expense.total.is_not(None))

    # 1) Mes actual, mes anterior y total histórico, en una sola consulta.
    month_sum = lambda since, until: func.coalesce(func.sum(case(  # noqa: E731
        ((day >= since) & (day <= until), Expense.total), else_=None)), 0)
    month_total, previous_total, counted_total, counted_count = db.execute(
        select(month_sum(month_start, today), month_sum(previous_start, month_start - timedelta(days=1)),
               func.coalesce(func.sum(Expense.total), 0), func.count())
        .where(*counted)
    ).one()

    # 2) Serie diaria de 30 días (los días sin gastos se completan con 0).
    by_day = dict(db.execute(
        select(day.label("day"), func.sum(Expense.total)).where(*counted, day >= series_start, day <= today)
        .group_by("day")
    ).all())
    daily = [(d, Decimal(by_day.get(d, ZERO))) for d in (series_start + timedelta(days=i) for i in range(DAYS))]

    # 3) Facturas por estado: cantidad y monto (todas las no eliminadas).
    by_status = {s.value: (0, ZERO) for s in ExpenseStatus}
    for status, count, total in db.execute(
        select(Expense.status, func.count(), func.coalesce(func.sum(Expense.total), 0)).where(*alive).group_by(Expense.status)
    ):
        by_status[ExpenseStatus(status).value] = (count, Decimal(total))

    # 4) Gasto por categoría y por empleado (lo que cuenta como gastado).
    by_category = [(name or "Sin categoría", Decimal(total)) for name, total in db.execute(
        select(Category.name, func.sum(Expense.total))
        .select_from(Expense).outerjoin(Category, Category.id == Expense.category_id)
        .where(*counted).group_by(Category.name).order_by(func.sum(Expense.total).desc())
    )]
    by_employee = [(eid, f"{first} {last}", count, Decimal(total)) for eid, first, last, count, total in db.execute(
        select(Employee.id, Employee.first_name, Employee.last_name, func.count(), func.sum(Expense.total))
        .join(Employee, Employee.id == Expense.employee_id)
        .where(*counted).group_by(Employee.id).order_by(func.sum(Expense.total).desc()).limit(TOP_EMPLOYEES)
    )]

    # 5) Empleados.
    employees_total, employees_active, employees_linked = db.execute(
        select(func.count(),
               func.count().filter(Employee.status == EmployeeStatus.ACTIVE),
               func.count().filter(Employee.telegram_user_id.is_not(None)))
        .where(Employee.company_id == company_id, Employee.deleted_at.is_(None))
    ).one()

    # 6) Viáticos abiertos: entregado, reintegrado y gastado de cada uno (dos consultas agrupadas).
    open_ids = select(Viatico.id).join(Employee, Employee.id == Viatico.employee_id).where(
        Employee.company_id == company_id, Viatico.status == ViaticoStatus.OPEN)
    delivered = defaultdict(lambda: ZERO)
    refunded = defaultdict(lambda: ZERO)
    for viatico_id, type_, total in db.execute(
        select(ViaticoMovement.viatico_id, ViaticoMovement.type, func.sum(ViaticoMovement.amount))
        .where(ViaticoMovement.viatico_id.in_(open_ids)).group_by(ViaticoMovement.viatico_id, ViaticoMovement.type)
    ):
        target = refunded if MovementType(type_) == MovementType.REFUND else delivered
        target[viatico_id] += Decimal(total)
    spent = defaultdict(lambda: ZERO, {vid: Decimal(total) for vid, total in db.execute(
        select(Expense.viatico_id, func.sum(Expense.total))
        .where(*counted, Expense.viatico_id.in_(open_ids)).group_by(Expense.viatico_id)
    )})
    viatico_ids = set(delivered) | set(refunded)
    remaining = {vid: delivered[vid] - refunded[vid] - spent[vid] for vid in viatico_ids}

    # 7) Fotos de facturas que nunca quedaron en un gasto (sin contar las que no eran factura).
    unregistered = db.scalar(
        select(func.count()).select_from(Document).where(
            Document.company_id == company_id, Document.kind == DocumentKind.INVOICE_PHOTO,
            Document.expense_id.is_(None), Document.deleted_at.is_(None),
            Document.processing_status != ProcessingStatus.NOT_INVOICE)
    )

    return DashboardStats(
        today=today,
        month_total=Decimal(month_total),
        previous_month_total=Decimal(previous_total),
        daily=daily,
        by_status=by_status,
        by_category=by_category,
        by_employee=by_employee,
        employees_total=employees_total,
        employees_active=employees_active,
        employees_linked=employees_linked,
        viaticos_open=len(viatico_ids),
        viaticos_delivered=sum((delivered[v] - refunded[v] for v in viatico_ids), ZERO),
        viaticos_spent=sum((spent[v] for v in viatico_ids), ZERO),
        viaticos_remaining=sum(remaining.values(), ZERO),
        viaticos_overspent=sum(1 for r in remaining.values() if r < 0),
        unregistered_photos=unregistered,
        counted_total=Decimal(counted_total),
        counted_count=counted_count,
    )
