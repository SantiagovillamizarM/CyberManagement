import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDPrimaryKey, enum_column
from app.models.enums import NotificationStatus


class Notification(UUIDPrimaryKey, Base):
    """Bandeja de salida ("outbox") de avisos por Telegram.

    Python decide QUÉ avisar y lo guarda aquí en la MISMA transacción que el cambio que lo
    origina (aprobar un gasto, recargar un viático...). n8n los toma y los envía: si n8n o
    Telegram están caídos, el aviso espera y se reintenta; nunca se pierde ni se duplica.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # Lo que n8n consulta cada minuto: pendientes cuyo turno ya llegó.
        Index("ix_notifications_due", "status", "next_attempt_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )  # None = aviso para el administrador
    chat_id: Mapped[int] = mapped_column(BigInteger)
    kind: Mapped[str] = mapped_column(String(40))  # "expense_approved", "reminder_unfinished"...
    text: Mapped[str] = mapped_column(Text)  # HTML de Telegram, ya escapado
    # Evita duplicados (p. ej. un recordatorio por día): única si no es nula.
    dedup_key: Mapped[str | None] = mapped_column(String(150), unique=True)

    status: Mapped[NotificationStatus] = mapped_column(
        enum_column(NotificationStatus), default=NotificationStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(server_default=func.now())
    locked_until: Mapped[datetime | None]  # mientras n8n lo está enviando
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    sent_at: Mapped[datetime | None]
