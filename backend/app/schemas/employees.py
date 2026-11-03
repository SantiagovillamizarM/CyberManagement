import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.models.enums import EmployeeStatus

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
InternalId = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$"
    ),
]


class EmployeeCreate(BaseModel):
    first_name: Name
    last_name: Name
    internal_id: InternalId


class EmployeeUpdate(BaseModel):
    """Todos opcionales: solo se cambia lo que se envía."""

    first_name: Name | None = None
    last_name: Name | None = None
    internal_id: InternalId | None = None
    status: EmployeeStatus | None = None


class PendingCodeOut(BaseModel):
    hint: str
    expires_at: datetime
    expired: bool


class EmployeeOut(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
    internal_id: str
    status: EmployeeStatus
    telegram_linked: bool
    telegram_username: str | None
    linked_at: datetime | None
    pending_code: PendingCodeOut | None
    created_at: datetime


class EmployeeList(BaseModel):
    items: list[EmployeeOut]
    total: int


class IssuedCodeOut(BaseModel):
    code: str = Field(description="Se muestra UNA sola vez. Entrégalo al empleado.")
    hint: str
    expires_at: datetime
