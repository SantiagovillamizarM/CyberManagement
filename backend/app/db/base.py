"""Clase base y piezas reutilizables para todos los modelos."""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, Enum, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Nombres predecibles para índices y restricciones. Alembic los necesita para
# poder modificarlos o borrarlos en migraciones futuras.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# Dinero: hasta 999.999.999.999,99 COP. NUNCA usar float para montos.
Money = Numeric(14, 2, asdecimal=True)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {Decimal: Money, datetime: DateTime(timezone=True)}


class UUIDPrimaryKey:
    # UUID en lugar de 1, 2, 3...: evita que alguien adivine registros por la URL.
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


def enum_column(enum_cls: type[StrEnum]) -> Enum:
    """Guarda el enum como texto + restricción CHECK (más fácil de migrar que un ENUM nativo)."""
    return Enum(
        enum_cls,
        name=enum_cls.__name__.lower(),
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda e: [member.value for member in e],
        validate_strings=True,
    )
