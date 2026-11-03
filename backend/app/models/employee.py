import uuid
from datetime import datetime

from sqlalchemy import BigInteger, ForeignKey, Index, String, UniqueConstraint, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_column
from app.models.enums import EmployeeStatus


class Employee(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("company_id", "internal_id"),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    internal_id: Mapped[str] = mapped_column(String(50))
    status: Mapped[EmployeeStatus] = mapped_column(
        enum_column(EmployeeStatus), default=EmployeeStatus.ACTIVE
    )

    # Vinculación con Telegram (vacío hasta que el empleado use su código).
    telegram_user_id: Mapped[int | None] = mapped_column(BigInteger, unique=True)
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    telegram_username: Mapped[str | None] = mapped_column(String(64))
    linked_at: Mapped[datetime | None]

    # Borrado lógico: los gastos históricos deben conservarse.
    deleted_at: Mapped[datetime | None]

    registration_codes: Mapped[list["RegistrationCode"]] = relationship(back_populates="employee")


class RegistrationCode(UUIDPrimaryKey, Base):
    """Código de un solo uso para vincular Telegram. Nunca se guarda en texto plano."""

    __tablename__ = "registration_codes"
    __table_args__ = (
        # Un empleado solo puede tener un código vigente a la vez.
        Index(
            "uq_registration_codes_one_active_per_employee",
            "employee_id",
            unique=True,
            postgresql_where=text("used_at IS NULL AND revoked_at IS NULL"),
        ),
    )

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)  # HMAC-SHA256
    code_hint: Mapped[str] = mapped_column(String(32))  # ej. "JHON-…M9XT" para el panel
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
    used_by_telegram_user_id: Mapped[int | None] = mapped_column(BigInteger)
    revoked_at: Mapped[datetime | None]

    employee: Mapped[Employee] = relationship(back_populates="registration_codes")


class DataConsent(UUIDPrimaryKey, Base):
    """Autorización de tratamiento de datos personales (Ley 1581 de 2012)."""

    __tablename__ = "data_consents"

    employee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), index=True
    )
    policy_version: Mapped[str] = mapped_column(String(20))
    channel: Mapped[str] = mapped_column(String(20))  # "telegram", "web"...
    accepted_at: Mapped[datetime] = mapped_column(server_default=func.now())
