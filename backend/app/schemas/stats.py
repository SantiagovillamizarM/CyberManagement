import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class MonthOut(BaseModel):
    total: Decimal
    previous_total: Decimal
    change: float | None  # variación frente al mes anterior (0.25 = +25 %); None si el anterior fue 0


class DayOut(BaseModel):
    date: date
    total: Decimal


class StatusOut(BaseModel):
    count: int
    total: Decimal


class AmountOut(BaseModel):
    name: str
    total: Decimal


class EmployeeAmountOut(AmountOut):
    employee_id: uuid.UUID
    count: int


class EmployeesOut(BaseModel):
    total: int
    active: int
    telegram_linked: int


class ViaticosOut(BaseModel):
    open: int
    delivered: Decimal  # entregado − reintegrado, de los viáticos abiertos
    spent: Decimal
    remaining: Decimal
    overspent: int  # cuántos gastaron más de lo entregado


class DashboardOut(BaseModel):
    today: date
    month: MonthOut
    daily: list[DayOut]  # 30 días, del más antiguo a hoy
    by_status: dict[str, StatusOut]
    by_category: list[AmountOut]
    by_employee: list[EmployeeAmountOut]  # los 8 que más han gastado
    employees: EmployeesOut
    viaticos: ViaticosOut
    unregistered_photos: int
    counted_total: Decimal  # todo lo gastado (histórico)
    counted_count: int
