import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.enums import DataSource, ExpenseStatus, LocationSource, PaymentSource
from app.schemas.common import EmployeeRef, Money


def _text(max_length: int):
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=max_length)]


Latitude = Annotated[Decimal, Field(ge=-90, le=90)]
Longitude = Annotated[Decimal, Field(ge=-180, le=180)]


class ExpenseItemIn(BaseModel):
    description: _text(255)
    quantity: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=3)] = Decimal("1")
    unit_price: Money | None = None
    line_total: Money | None = None


class ExpenseFields(BaseModel):
    """Campos editables comunes a crear y actualizar."""

    description: _text(2000) | None = None
    invoice_number: _text(50) | None = None
    cufe: _text(120) | None = None
    issue_date: date | None = None
    issue_time: time | None = None
    subtotal: Money | None = None
    tax_total: Money | None = None
    total: Money | None = None
    category_id: uuid.UUID | None = None
    viatico_id: uuid.UUID | None = None
    payment_source: PaymentSource | None = None
    location_lat: Latitude | None = None
    location_lon: Longitude | None = None
    location_text: _text(255) | None = None
    supplier_nit: _text(20) | None = Field(default=None, examples=["900.123.456-8"])
    supplier_name: _text(200) | None = None


class ExpenseCreate(ExpenseFields):
    employee_id: uuid.UUID
    items: list[ExpenseItemIn] = Field(default_factory=list, max_length=100)


class ExpenseUpdate(ExpenseFields):
    """Solo se modifican los campos enviados. Enviar `null` borra el valor."""

    version: int = Field(description="Versión que el usuario estaba viendo (bloqueo optimista)")
    items: list[ExpenseItemIn] | None = Field(default=None, max_length=100)


class RejectRequest(BaseModel):
    reason: _text(1000)


class SupplierOut(BaseModel):
    nit: str
    dv: str | None
    name: str


class CategoryOut(BaseModel):
    id: uuid.UUID
    name: str


class ExpenseItemOut(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal | None
    line_total: Decimal | None


class ExpenseOut(BaseModel):
    id: uuid.UUID
    employee: EmployeeRef
    viatico_id: uuid.UUID | None
    category: CategoryOut | None
    supplier: SupplierOut | None
    description: str | None
    invoice_number: str | None
    cufe: str | None
    issue_date: date | None
    issue_time: time | None
    received_at: datetime
    subtotal: Decimal | None
    tax_total: Decimal | None
    total: Decimal | None
    currency: str
    payment_source: PaymentSource | None
    is_reimbursable: bool
    location_lat: Decimal | None
    location_lon: Decimal | None
    location_text: str | None
    location_source: LocationSource
    status: ExpenseStatus
    data_source: DataSource
    rejection_reason: str | None
    reviewed_at: datetime | None
    version: int
    items: list[ExpenseItemOut]
    warnings: list[str]
    created_at: datetime
    updated_at: datetime


class ExpenseList(BaseModel):
    items: list[ExpenseOut]
    total: int
