"""Documentos (fotos de facturas, RUT, PDF): guardado seguro y consulta.

REGLA FUNDAMENTAL: la foto se guarda ANTES de cualquier análisis con IA y
existe por sí misma, aunque todavía no haya un gasto asociado.
"""

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Company, Document, Employee, Expense
from app.models.enums import DocumentKind, LocationSource, ProcessingStatus
from app.services import audit
from app.services.audit import Actor
from app.storage import Storage
from app.storage.inspection import FileInfo, inspect_file

logger = logging.getLogger(__name__)


class DocumentNotFoundError(Exception):
    pass


@dataclass(frozen=True)
class StoredDocument:
    document: Document
    created: bool  # False = ya existía (mismo SHA-256): no se duplicó
    info: FileInfo | None = None


def _retention_until(db: Session, company_id: uuid.UUID, received_at: datetime):
    years = db.get(Company, company_id).retention_years
    try:
        return received_at.date().replace(year=received_at.year + years)
    except ValueError:  # 29 de febrero -> 28 de febrero
        return received_at.date().replace(year=received_at.year + years, day=28)


def _storage_key(company_id: uuid.UUID, document_id: uuid.UUID, extension: str, now: datetime) -> str:
    # El nombre original NUNCA se usa en la ruta: solo identificadores generados aquí.
    return f"{company_id}/{now:%Y}/{now:%m}/{document_id}.{extension}"


def store_document(
    db: Session,
    storage: Storage,
    *,
    company_id: uuid.UUID,
    data: bytes,
    kind: DocumentKind,
    actor: Actor,
    employee_id: uuid.UUID | None = None,
    original_filename: str | None = None,
    telegram_file_unique_id: str | None = None,
) -> StoredDocument:
    """Valida y guarda el archivo, y crea su registro.

    Lanza UnsupportedFileError, InvalidFileError o FileTooLargeError si el archivo no es válido.
    """
    info = inspect_file(data)  # valida ANTES de tocar el disco
    sha256 = hashlib.sha256(data).hexdigest()

    existing = db.scalar(
        select(Document).where(Document.company_id == company_id, Document.sha256 == sha256)
    )
    if existing is not None:
        logger.info("Documento duplicado (sha256 ya registrado, document_id=%s)", existing.id)
        return StoredDocument(existing, created=False, info=info)

    now = datetime.now(UTC)
    document_id = uuid.uuid4()
    key = _storage_key(company_id, document_id, info.extension, now)

    # 1) Disco primero. Si falla, se lanza la excepción y no se crea nada en la BD.
    storage.save(key, data)
    # 2) Luego la BD. Si falla, se borra el archivo: no quedan huérfanos.
    try:
        document = Document(
            id=document_id,
            company_id=company_id,
            employee_id=employee_id,
            kind=kind,
            processing_status=ProcessingStatus.RECEIVED,
            storage_key=key,
            mime_type=info.mime_type,
            size_bytes=len(data),
            sha256=sha256,
            width=info.width,
            height=info.height,
            original_filename=(original_filename or "")[:255] or None,
            telegram_file_unique_id=telegram_file_unique_id,
            exif=info.exif,
            received_at=now,
            retention_until=_retention_until(db, company_id, now),
        )
        with db.begin_nested():  # punto de guardado: un fallo aquí no arruina la transacción
            db.add(document)
            db.flush()
        audit.log(
            db,
            company_id=company_id,
            entity_type="document",
            entity_id=document.id,
            action="create",
            actor=actor,
            new_value={"kind": kind, "size_bytes": len(data), "sha256": sha256},
        )
    except Exception:
        storage.delete(key)
        logger.exception("No se pudo registrar el documento; archivo eliminado (key=%s)", key)
        raise
    return StoredDocument(document, created=True, info=info)


def get_document(db: Session, company_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = db.get(Document, document_id)
    if document is None or document.company_id != company_id or document.deleted_at is not None:
        raise DocumentNotFoundError
    return document


def list_documents(
    db: Session,
    company_id: uuid.UUID,
    *,
    employee_id: uuid.UUID | None = None,
    expense_id: uuid.UUID | None = None,
    processing_status: ProcessingStatus | None = None,
    kind: DocumentKind | None = None,
    unattached: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Document], int]:
    query = select(Document).where(Document.company_id == company_id, Document.deleted_at.is_(None))
    if employee_id:
        query = query.where(Document.employee_id == employee_id)
    if expense_id:
        query = query.where(Document.expense_id == expense_id)
    if processing_status:
        query = query.where(Document.processing_status == processing_status)
    if kind:
        query = query.where(Document.kind == kind)
    if unattached:  # fotos que nunca quedaron en un gasto (p. ej. el empleado no terminó el registro)
        query = query.where(Document.expense_id.is_(None))
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Document.received_at.desc()).limit(limit).offset(offset))
    return list(items), total


def attach_to_expense(db: Session, document: Document, expense: Expense, actor: Actor) -> None:
    """Asocia el documento al gasto. Si el gasto no tiene ubicación y la foto trae GPS, la usa.

    Prioridad de ubicación (requerimiento 7): Telegram > EXIF > manual. Nunca se
    sobrescribe una ubicación que ya existe.
    """
    if document.expense_id == expense.id:
        return
    old_expense_id = document.expense_id
    document.expense_id = expense.id
    if document.employee_id is None:
        document.employee_id = expense.employee_id
    audit.log(
        db,
        company_id=document.company_id,
        entity_type="document",
        entity_id=document.id,
        action="attach",
        actor=actor,
        field="expense_id",
        old_value=old_expense_id,
        new_value=expense.id,
    )

    gps = (document.exif or {}).get("gps")
    if gps and expense.location_source == LocationSource.NONE:
        audit.apply_changes(
            db,
            expense,
            {
                "location_lat": Decimal(gps["lat"]),
                "location_lon": Decimal(gps["lon"]),
                "location_source": LocationSource.EXIF,
            },
            company_id=expense.company_id,
            entity_type="expense",
            actor=actor,
        )
    db.flush()


def employee_by_telegram(db: Session, telegram_user_id: int) -> Employee | None:
    return db.scalar(select(Employee).where(Employee.telegram_user_id == telegram_user_id))
