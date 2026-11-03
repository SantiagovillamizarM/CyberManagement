import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey


class Report(UUIDPrimaryKey, Base):
    """Informe PDF generado (con los filtros usados, para poder reproducirlo)."""

    __tablename__ = "reports"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    generated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    filters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    storage_key: Mapped[str] = mapped_column(String(255), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
