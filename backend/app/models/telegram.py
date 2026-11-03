import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BotSession(Base):
    """Paso actual de la conversación de cada usuario de Telegram."""

    __tablename__ = "bot_sessions"

    telegram_user_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE")
    )
    state: Mapped[str] = mapped_column(String(50), default="idle")
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    draft_expense_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("expenses.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime | None]


class TelegramEvent(Base):
    """Cada mensaje recibido. update_id único evita procesar dos veces el mismo mensaje."""

    __tablename__ = "telegram_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    update_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(40))  # "photo", "text", "registration_failed"...
    received_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
