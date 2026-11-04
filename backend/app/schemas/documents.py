import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from app.models import Document
from app.models.enums import DocumentKind, ProcessingStatus


class GpsOut(BaseModel):
    lat: Decimal
    lon: Decimal


class DocumentOut(BaseModel):
    id: uuid.UUID
    kind: DocumentKind
    processing_status: ProcessingStatus
    mime_type: str
    size_bytes: int
    sha256: str
    width: int | None
    height: int | None
    original_filename: str | None
    employee_id: uuid.UUID | None
    expense_id: uuid.UUID | None
    received_at: datetime
    retention_until: date | None
    gps: GpsOut | None  # None = "No disponible"
    exif: dict[str, Any] | None

    @classmethod
    def from_model(cls, document: Document) -> "DocumentOut":
        gps = (document.exif or {}).get("gps")
        return cls.model_validate(
            {
                **{f: getattr(document, f) for f in cls.model_fields if f != "gps"},
                "gps": GpsOut(**gps) if gps else None,
            }
        )


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int


class UploadResult(BaseModel):
    document: DocumentOut
    duplicate: bool  # True = ese mismo archivo ya estaba guardado
