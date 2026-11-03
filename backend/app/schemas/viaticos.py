import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from app.models.enums import MovementSource, MovementType, ViaticoStatus
from app.schemas.common import EmployeeRef, Money, SignedMoney
from app.services.viaticos import Settlement

Note = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class ViaticoCreate(BaseModel):
    employee_id: uuid.UUID
    initial_amount: Annotated[Decimal, Field(gt=0, max_digits=14, decimal_places=2)]
    purpose: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = None


class MovementCreate(BaseModel):
    type: Literal[MovementType.ADDITIONAL, MovementType.REFUND, MovementType.ADJUSTMENT]
    amount: SignedMoney  # solo los ajustes pueden ser negativos (lo valida el servicio)
    note: Note | None = None


class MovementOut(BaseModel):
    id: uuid.UUID
    type: MovementType
    amount: Decimal
    source: MovementSource
    note: str | None
    created_at: datetime


class BalanceOut(BaseModel):
    delivered: Money
    refunded: Money
    spent: Money
    spent_approved: Money
    remaining: SignedMoney
    settlement: Settlement


class ViaticoOut(BaseModel):
    id: uuid.UUID
    employee: EmployeeRef
    purpose: str | None
    status: ViaticoStatus
    opened_at: datetime
    closed_at: datetime | None
    balance: BalanceOut
    movements: list[MovementOut]
