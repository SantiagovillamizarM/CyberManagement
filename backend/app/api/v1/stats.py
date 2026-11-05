"""Estadísticas para el dashboard: se calculan en la base de datos (paso 7.4)."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import READERS, require_roles
from app.db.session import get_db
from app.models import AdminUser
from app.schemas.stats import (
    AmountOut, DashboardOut, DayOut, EmployeeAmountOut, EmployeesOut, MonthOut, StatusOut, ViaticosOut,
)
from app.services import stats as service

router = APIRouter(prefix="/stats", tags=["estadísticas"])


@router.get("/dashboard", response_model=DashboardOut)
def dashboard(admin: AdminUser = Depends(require_roles(*READERS)), db: Session = Depends(get_db)) -> DashboardOut:
    s = service.dashboard(db, admin.company_id)
    change = float((s.month_total - s.previous_month_total) / s.previous_month_total) if s.previous_month_total else None
    return DashboardOut(
        today=s.today,
        month=MonthOut(total=s.month_total, previous_total=s.previous_month_total, change=change),
        daily=[DayOut(date=d, total=t) for d, t in s.daily],
        by_status={k: StatusOut(count=c, total=t) for k, (c, t) in s.by_status.items()},
        by_category=[AmountOut(name=n, total=t) for n, t in s.by_category],
        by_employee=[EmployeeAmountOut(employee_id=i, name=n, count=c, total=t) for i, n, c, t in s.by_employee],
        employees=EmployeesOut(total=s.employees_total, active=s.employees_active, telegram_linked=s.employees_linked),
        viaticos=ViaticosOut(open=s.viaticos_open, delivered=s.viaticos_delivered, spent=s.viaticos_spent,
                             remaining=s.viaticos_remaining, overspent=s.viaticos_overspent),
        unregistered_photos=s.unregistered_photos,
        counted_total=s.counted_total,
        counted_count=s.counted_count,
    )
