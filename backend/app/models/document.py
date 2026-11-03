import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamps, UUIDPrimaryKey, enum_column
from app.models.enums import DocumentKind, ExtractionOutcome, ProcessingStatus


class Document(UUIDPrimaryKey, Timestamps, Base):
    """Archivo recibido (foto de factura, RUT...).

    Existe por sí mismo, aunque no haya un gasto asociado: así nunca se pierde
    una foto si la IA falla. El archivo vive en disco; aquí solo su referencia.
    """

    __tablename__ = "documents"
    __table_args__ = (UniqueConstraint("company_id", "sha256"),)  # misma imagen = duplicado

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("employees.id"), index=True)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("expenses.id", ondelete="SET NULL"), index=True
    )
    supplier_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("suppliers.id"))  # RUT

    kind: Mapped[DocumentKind] = mapped_column(enum_column(DocumentKind))
    processing_status: Mapped[ProcessingStatus] = mapped_column(
        enum_column(ProcessingStatus), default=ProcessingStatus.RECEIVED, index=True
    )

    storage_key: Mapped[str] = mapped_column(String(255), unique=True)  # ruta interna
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None]
    height: Mapped[int | None]
    original_filename: Mapped[str | None] = mapped_column(String(255))
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(100))
    exif: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    received_at: Mapped[datetime] = mapped_column(server_default=func.now())
    retention_until: Mapped[date | None]
    deleted_at: Mapped[datetime | None]

    extractions: Mapped[list["AIExtraction"]] = relationship(
        back_populates="document", order_by="AIExtraction.created_at"
    )


class AIExtraction(UUIDPrimaryKey, Base):
    """Registro técnico de CADA intento de análisis con IA (también los fallidos)."""

    __tablename__ = "ai_extractions"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[ExtractionOutcome] = mapped_column(enum_column(ExtractionOutcome))
    is_invoice: Mapped[bool | None]
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))  # 0.000 a 1.000
    raw_response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None]
    tokens_in: Mapped[int | None]
    tokens_out: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="extractions")
