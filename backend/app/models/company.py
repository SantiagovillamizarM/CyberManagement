from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UUIDPrimaryKey


class Company(UUIDPrimaryKey, Timestamps, Base):
    __tablename__ = "companies"

    name: Mapped[str] = mapped_column(String(200))
    nit: Mapped[str | None] = mapped_column(String(20))
    timezone: Mapped[str] = mapped_column(String(64), default="America/Bogota")
    # Años de conservación de soportes contables (Código de Comercio, art. 60).
    retention_years: Mapped[int] = mapped_column(default=10)
