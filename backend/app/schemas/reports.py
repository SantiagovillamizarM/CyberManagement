import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ExpenseStatus


class ReportRequest(BaseModel):
    date_from: date
    date_to: date
    employee_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    # Vacío = lo que cuenta como gastado (enviado, en revisión, aprobado y cerrado).
    statuses: list[ExpenseStatus] = Field(default_factory=list, max_length=10)
    include_photos: bool = True


class ReportOut(BaseModel):
    id: uuid.UUID
    filters: dict[str, Any]
    summary: dict[str, Any] | None
    size_bytes: int | None
    generated_by: str
    created_at: datetime
    file_url: str  # vista previa (inline); agregar ?download=true para descargar
