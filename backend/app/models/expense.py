import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_column
from app.models.enums import DataSource, ExpenseStatus, LocationSource, PaymentSource


class Category(UUIDPrimaryKey, Base):
    """Tipo de gasto: alimentación, transporte, hospedaje..."""

    __tablename__ = "categories"
    __table_args__ = (UniqueConstraint("company_id", "name"),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    name: Mapped[str] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(default=True)


class Supplier(UUIDPrimaryKey, Timestamps, Base):
    """Establecimiento o proveedor, identificado por su NIT."""

    __tablename__ = "suppliers"
    __table_args__ = (UniqueConstraint("company_id", "nit"),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"))
    nit: Mapped[str] = mapped_column(String(15))  # solo dígitos, sin DV
    dv: Mapped[str | None] = mapped_column(String(1))  # dígito de verificación
    name: Mapped[str] = mapped_column(String(200))


class Expense(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("total IS NULL OR total >= 0", name="total_not_negative"),
        # Protección contra facturas duplicadas.
        Index(
            "uq_expenses_cufe",
            "company_id",
            "cufe",
            unique=True,
            postgresql_where=text("cufe IS NOT NULL AND deleted_at IS NULL"),
        ),
        Index(
            "uq_expenses_supplier_invoice_number",
            "company_id",
            "supplier_id",
            "invoice_number",
            unique=True,
            postgresql_where=text(
                "supplier_id IS NOT NULL AND invoice_number IS NOT NULL AND deleted_at IS NULL"
            ),
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employees.id"), index=True)
    viatico_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("viaticos.id"), index=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("categories.id"))
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"), index=True)

    # --- Datos de la factura (pueden estar vacíos mientras es borrador) ---
    description: Mapped[str | None] = mapped_column(Text)
    invoice_number: Mapped[str | None] = mapped_column(String(50))
    cufe: Mapped[str | None] = mapped_column(String(120))  # factura electrónica DIAN
    issue_date: Mapped[date | None]  # fecha de EXPEDICIÓN (impresa en la factura)
    issue_time: Mapped[time | None]
    subtotal: Mapped[Decimal | None]
    tax_total: Mapped[Decimal | None]
    total: Mapped[Decimal | None]
    currency: Mapped[str] = mapped_column(String(3), default="COP")

    # Fecha de RECEPCIÓN (cuando el empleado lo envió). Distinta de issue_date.
    received_at: Mapped[datetime] = mapped_column(server_default=func.now())

    payment_source: Mapped[PaymentSource | None] = mapped_column(enum_column(PaymentSource))
    is_reimbursable: Mapped[bool] = mapped_column(default=False)

    # --- Ubicación: nunca se inventa. Sin datos -> source = "none". ---
    location_lat: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    location_lon: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    location_text: Mapped[str | None] = mapped_column(String(255))
    location_source: Mapped[LocationSource] = mapped_column(
        enum_column(LocationSource), default=LocationSource.NONE
    )

    # --- Estado ---
    status: Mapped[ExpenseStatus] = mapped_column(
        enum_column(ExpenseStatus), default=ExpenseStatus.DRAFT, index=True
    )
    data_source: Mapped[DataSource] = mapped_column(
        enum_column(DataSource), default=DataSource.MANUAL
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    reviewed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    reviewed_at: Mapped[datetime | None]

    deleted_at: Mapped[datetime | None]

    # Bloqueo optimista: si dos personas editan a la vez, la segunda recibe un
    # error en lugar de sobrescribir los cambios de la primera sin saberlo.
    version: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": version}

    items: Mapped[list["ExpenseItem"]] = relationship(
        back_populates="expense", cascade="all, delete-orphan", order_by="ExpenseItem.position"
    )


class ExpenseItem(UUIDPrimaryKey, Base):
    """Línea de una factura (producto o servicio)."""

    __tablename__ = "expense_items"
    __table_args__ = (CheckConstraint("quantity > 0", name="quantity_positive"),)

    expense_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expenses.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(default=0)
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), default=Decimal("1"))
    unit_price: Mapped[Decimal | None]
    line_total: Mapped[Decimal | None]

    expense: Mapped[Expense] = relationship(back_populates="items")
