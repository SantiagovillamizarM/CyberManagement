import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_column
from app.models.enums import MovementSource, MovementType, ViaticoStatus


class Viatico(UUIDPrimaryKey, Timestamps, Base):
    """Dinero entregado a un empleado. Un empleado puede tener varios abiertos.

    El saldo NO se guarda: se calcula a partir de movimientos y gastos, para
    que nunca quede desincronizado.
    """

    __tablename__ = "viaticos"

    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    purpose: Mapped[str | None] = mapped_column(String(200))  # ej. "Viaje a Medellín"
    status: Mapped[ViaticoStatus] = mapped_column(
        enum_column(ViaticoStatus), default=ViaticoStatus.OPEN
    )
    opened_at: Mapped[datetime] = mapped_column(server_default=func.now())
    closed_at: Mapped[datetime | None]
    closed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))

    movements: Mapped[list["ViaticoMovement"]] = relationship(
        back_populates="viatico", order_by="ViaticoMovement.created_at"
    )


class ViaticoMovement(UUIDPrimaryKey, Base):
    """Entrada o salida de dinero del viático (no son facturas)."""

    __tablename__ = "viatico_movements"
    __table_args__ = (
        # Solo los ajustes pueden ser negativos; nada puede valer cero.
        CheckConstraint("amount <> 0 AND (amount > 0 OR type = 'adjustment')", name="amount_sign"),
    )

    viatico_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("viaticos.id", ondelete="CASCADE"), index=True
    )
    type: Mapped[MovementType] = mapped_column(enum_column(MovementType))
    amount: Mapped[Decimal]
    source: Mapped[MovementSource] = mapped_column(enum_column(MovementSource))
    note: Mapped[str | None] = mapped_column(Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    viatico: Mapped[Viatico] = relationship(back_populates="movements")
