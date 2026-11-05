"""Informes PDF (Fase 8): reunir los datos, generar el PDF, guardarlo y registrarlo.

Mismas reglas que el dashboard (services/stats.py): el día de un gasto es la fecha de la
factura o, si falta, la de recepción en hora de Colombia. Si no se eligen estados, el informe
incluye lo que cuenta como gastado (enviado, en revisión, aprobado y cerrado).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AdminUser, Category, Company, Document, Employee, Expense, Report, Supplier, Viatico
from app.models.enums import DocumentKind, ExpenseStatus
from app.services import audit, viaticos
from app.services.audit import Actor
from app.services.stats import _expense_day
from app.services.viaticos import COUNTED_STATUSES, Balance
from app.storage import Storage

ZERO = Decimal("0.00")
MAX_DAYS = 366  # un informe cubre como máximo un año (protege al servidor de informes gigantes)
MAX_EXPENSES = 2000


class ReportRuleError(ValueError):
    """Filtros inválidos; el mensaje se puede mostrar al usuario."""


class ReportNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class ReportFilters:
    date_from: date
    date_to: date
    employee_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    statuses: tuple[ExpenseStatus, ...] = ()
    include_photos: bool = True

    def effective_statuses(self) -> tuple[ExpenseStatus, ...]:
        return self.statuses or tuple(COUNTED_STATUSES)

    def to_json(self) -> dict[str, Any]:
        return {
            "date_from": self.date_from.isoformat(), "date_to": self.date_to.isoformat(),
            "employee_id": str(self.employee_id) if self.employee_id else None,
            "category_id": str(self.category_id) if self.category_id else None,
            "statuses": [s.value for s in self.effective_statuses()],
            "include_photos": self.include_photos,
        }


@dataclass
class ExpenseRow:
    expense: Expense
    employee_name: str
    category: str | None
    supplier: Supplier | None
    day: date
    photo: Document | None


@dataclass
class ViaticoRow:
    viatico: Viatico
    employee_name: str
    balance: Balance


@dataclass
class ReportData:
    company: Company
    filters: ReportFilters
    generated_at: datetime
    generated_by: str
    employee_name: str | None  # si el informe es de un solo empleado
    category_name: str | None
    rows: list[ExpenseRow]
    viaticos: list[ViaticoRow]
    truncated: bool = False  # hubo más gastos que MAX_EXPENSES
    total: Decimal = ZERO
    subtotal: Decimal = ZERO
    tax_total: Decimal = ZERO
    by_status: dict[str, tuple[int, Decimal]] = field(default_factory=dict)
    by_category: list[tuple[str, int, Decimal]] = field(default_factory=list)
    by_employee: list[tuple[str, int, Decimal]] = field(default_factory=list)

    @property
    def viaticos_delivered(self) -> Decimal:
        return sum((v.balance.delivered - v.balance.refunded for v in self.viaticos), ZERO)

    @property
    def viaticos_spent(self) -> Decimal:
        return sum((v.balance.spent for v in self.viaticos), ZERO)

    @property
    def viaticos_remaining(self) -> Decimal:
        return sum((v.balance.remaining for v in self.viaticos), ZERO)

    def summary(self) -> dict[str, Any]:
        return {"expenses": len(self.rows), "total": str(self.total), "photos": sum(1 for r in self.rows if r.photo),
                "viaticos": len(self.viaticos), "viaticos_remaining": str(self.viaticos_remaining),
                "truncated": self.truncated}


def validate(filters: ReportFilters) -> None:
    if filters.date_to < filters.date_from:
        raise ReportRuleError("La fecha final no puede ser anterior a la inicial")
    if (filters.date_to - filters.date_from).days >= MAX_DAYS:
        raise ReportRuleError("Un informe puede cubrir como máximo un año")


def collect(db: Session, company_id: uuid.UUID, filters: ReportFilters, *, generated_by: str) -> ReportData:
    validate(filters)
    company = db.get(Company, company_id)
    day = _expense_day()
    query = (
        select(Expense, Employee, Category.name, Supplier, day.label("day"))
        .join(Employee, Employee.id == Expense.employee_id)
        .outerjoin(Category, Category.id == Expense.category_id)
        .outerjoin(Supplier, Supplier.id == Expense.supplier_id)
        .where(Expense.company_id == company_id, Expense.deleted_at.is_(None),
               Expense.status.in_(filters.effective_statuses()), day >= filters.date_from, day <= filters.date_to)
        .order_by(day, Expense.received_at)
        .limit(MAX_EXPENSES + 1)
    )
    if filters.employee_id:
        query = query.where(Expense.employee_id == filters.employee_id)
    if filters.category_id:
        query = query.where(Expense.category_id == filters.category_id)
    results = db.execute(query).all()
    truncated = len(results) > MAX_EXPENSES
    results = results[:MAX_EXPENSES]

    photos: dict[uuid.UUID, Document] = {}
    if filters.include_photos and results:
        for doc in db.scalars(
            select(Document).where(Document.expense_id.in_([r[0].id for r in results]),
                                   Document.kind == DocumentKind.INVOICE_PHOTO, Document.deleted_at.is_(None))
            .order_by(Document.received_at)
        ):
            photos.setdefault(doc.expense_id, doc)

    rows = [ExpenseRow(expense=e, employee_name=f"{emp.first_name} {emp.last_name}", category=cat, supplier=sup,
                       day=d, photo=photos.get(e.id)) for e, emp, cat, sup, d in results]

    data = ReportData(
        company=company, filters=filters, generated_at=datetime.now(UTC), generated_by=generated_by,
        employee_name=_employee_name(db, company_id, filters.employee_id),
        category_name=(db.get(Category, filters.category_id).name
                       if filters.category_id and db.get(Category, filters.category_id) else None),
        rows=rows, viaticos=_viaticos(db, company_id, filters), truncated=truncated,
    )
    _totals(data)
    return data


def _employee_name(db: Session, company_id: uuid.UUID, employee_id: uuid.UUID | None) -> str | None:
    if not employee_id:
        return None
    employee = db.get(Employee, employee_id)
    if employee is None or employee.company_id != company_id:
        raise ReportRuleError("El empleado no existe")
    return f"{employee.first_name} {employee.last_name}"


def _viaticos(db: Session, company_id: uuid.UUID, filters: ReportFilters) -> list[ViaticoRow]:
    """Viáticos vigentes en algún momento del período (abiertos antes de que terminara y no cerrados antes)."""
    tz = get_settings().timezone
    opened = cast(func.timezone(tz, Viatico.opened_at), Date)
    closed = cast(func.timezone(tz, Viatico.closed_at), Date)
    query = (
        select(Viatico, Employee)
        .join(Employee, Employee.id == Viatico.employee_id)
        .where(Employee.company_id == company_id, opened <= filters.date_to,
               or_(Viatico.closed_at.is_(None), closed >= filters.date_from))
        .order_by(Employee.first_name, Viatico.opened_at)
    )
    if filters.employee_id:
        query = query.where(Viatico.employee_id == filters.employee_id)
    return [ViaticoRow(viatico=v, employee_name=f"{e.first_name} {e.last_name}",
                       balance=viaticos.compute_balance(db, v)) for v, e in db.execute(query)]


def _totals(data: ReportData) -> None:
    by_status: dict[str, list] = defaultdict(lambda: [0, ZERO])
    by_category: dict[str, list] = defaultdict(lambda: [0, ZERO])
    by_employee: dict[str, list] = defaultdict(lambda: [0, ZERO])
    for row in data.rows:
        e = row.expense
        amount = e.total or ZERO
        data.total += amount
        data.subtotal += e.subtotal or ZERO
        data.tax_total += e.tax_total or ZERO
        for bucket, key in ((by_status, e.status.value), (by_category, row.category or "Sin categoría"),
                            (by_employee, row.employee_name)):
            bucket[key][0] += 1
            bucket[key][1] += amount
    data.by_status = {k: (c, t) for k, (c, t) in by_status.items()}
    ranked = lambda d: sorted(((k, c, t) for k, (c, t) in d.items()), key=lambda x: x[2], reverse=True)  # noqa: E731
    data.by_category = ranked(by_category)
    data.by_employee = ranked(by_employee)


# --- Generar, guardar y consultar ---


def generate(db: Session, storage: Storage, admin: AdminUser, filters: ReportFilters,
             actor: Actor) -> tuple[Report, ReportData]:
    """Lo pide un administrador desde el panel."""
    data = collect(db, admin.company_id, filters, generated_by=admin.full_name)
    return _store(db, storage, data, generated_by_id=admin.id, actor=actor), data


def generate_for_system(db: Session, storage: Storage, company_id: uuid.UUID, filters: ReportFilters,
                        extra_filters: dict[str, Any] | None = None) -> tuple[Report, ReportData]:
    """Lo pide n8n (WF7, informe mensual automático): no hay un administrador detrás."""
    data = collect(db, company_id, filters, generated_by="Envío automático mensual")
    return _store(db, storage, data, generated_by_id=None, actor=audit.SYSTEM, extra_filters=extra_filters), data


def _store(db: Session, storage: Storage, data: ReportData, *, generated_by_id: uuid.UUID | None,
           actor: Actor, extra_filters: dict[str, Any] | None = None) -> Report:
    from app.reports.pdf import build_pdf  # ReportLab solo se carga cuando se genera un informe

    pdf = build_pdf(data, storage)
    company_id = data.company.id
    report_id = uuid.uuid4()
    now = data.generated_at
    key = f"reports/{company_id}/{now:%Y}/{now:%m}/{report_id}.pdf"
    storage.save(key, pdf)  # disco primero; si la BD falla, se borra el archivo (no quedan huérfanos)
    try:
        report = Report(id=report_id, company_id=company_id, generated_by_id=generated_by_id,
                        filters={**data.filters.to_json(), **(extra_filters or {})}, summary=data.summary(),
                        storage_key=key, size_bytes=len(pdf))
        with db.begin_nested():
            db.add(report)
            db.flush()
        audit.log(db, company_id=company_id, entity_type="report", entity_id=report.id, action="create",
                  actor=actor, new_value={"filters": data.filters.to_json(), "summary": data.summary()})
    except Exception:
        storage.delete(key)
        raise
    return report


def get_report(db: Session, company_id: uuid.UUID, report_id: uuid.UUID) -> Report:
    report = db.get(Report, report_id)
    if report is None or report.company_id != company_id:
        raise ReportNotFoundError
    return report


def list_reports(db: Session, company_id: uuid.UUID, limit: int = 30) -> list[Report]:
    return list(db.scalars(select(Report).where(Report.company_id == company_id)
                           .order_by(Report.created_at.desc()).limit(limit)))


# --- Informe mensual automático (WF7 en n8n) ---

MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
          "noviembre", "diciembre")


def previous_month(today: date) -> date:
    """Primer día del mes anterior a `today`."""
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def month_label(first_day: date) -> str:
    return f"{MONTHS[first_day.month - 1]} de {first_day.year}"


def monthly_report(db: Session, storage: Storage, company_id: uuid.UUID, month: date) -> Report:
    """El informe de `month` completo. Si ya existe (n8n reintentó), se reutiliza: nunca se duplica."""
    key = month.strftime("%Y-%m")
    find = select(Report).where(Report.company_id == company_id, Report.filters["monthly"].astext == key)
    existing = db.scalar(find)
    if existing is not None:
        return existing
    last_day = (month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    try:
        report, _ = generate_for_system(db, storage, company_id, ReportFilters(date_from=month, date_to=last_day),
                                        extra_filters={"monthly": key})
    except IntegrityError:
        # Otra ejecución lo creó al mismo tiempo (índice único por empresa y mes): se usa ese.
        return db.scalar(find)
    return report


def mark_delivery(db: Session, report: Report, *, ok: bool, error: str | None = None) -> None:
    """Lo que informó n8n al enviarlo por Telegram (queda en el resumen del informe)."""
    summary = dict(report.summary or {})
    if ok:
        summary["telegram_sent_at"] = datetime.now(UTC).isoformat()
        summary.pop("telegram_error", None)
    else:
        summary["telegram_error"] = (error or "error desconocido")[:500]
    report.summary = summary
    db.flush()
