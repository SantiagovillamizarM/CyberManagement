"""Documentos desde el panel: listar, ver, descargar y adjuntar a un gasto."""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import EDITORS, READERS, client_ip, require_roles
from app.api.uploads import handle_file_errors, read_upload
from app.db.session import get_db
from app.models import AdminUser
from app.models.enums import DocumentKind, ProcessingStatus
from app.schemas.documents import DocumentList, DocumentOut, UploadResult
from app.services import documents as service
from app.services import expenses as expense_service
from app.services.audit import Actor
from app.storage import Storage, get_storage
from app.storage.base import StorageError

router = APIRouter(tags=["documentos"])

# Las imágenes se aíslan por completo: aunque alguien lograra colar código, no se ejecuta.
IMAGE_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox"


def _load(db: Session, admin: AdminUser, document_id: uuid.UUID):
    try:
        return service.get_document(db, admin.company_id, document_id)
    except service.DocumentNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Documento no encontrado")


@router.get("/documents", response_model=DocumentList)
def list_documents(
    employee_id: uuid.UUID | None = None,
    expense_id: uuid.UUID | None = None,
    processing_status: ProcessingStatus | None = None,
    kind: DocumentKind | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> DocumentList:
    items, total = service.list_documents(
        db, admin.company_id, employee_id=employee_id, expense_id=expense_id,
        processing_status=processing_status, kind=kind, limit=limit, offset=offset,
    )
    return DocumentList(items=[DocumentOut.from_model(d) for d in items], total=total)


@router.get("/documents/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: uuid.UUID,
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> DocumentOut:
    return DocumentOut.from_model(_load(db, admin, document_id))


@router.get("/documents/{document_id}/file")
def download_file(
    document_id: uuid.UUID,
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Response:
    document = _load(db, admin, document_id)
    try:
        data = storage.read(document.storage_key)
    except StorageError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El archivo no está disponible")
    extension = document.storage_key.rsplit(".", 1)[-1]
    headers = {
        # inline: el navegador lo muestra en lugar de descargarlo. El nombre es el ID,
        # nunca el nombre original (podría contener caracteres peligrosos).
        "Content-Disposition": f'inline; filename="{document.id}.{extension}"',
    }
    if document.mime_type.startswith("image/"):
        headers["Content-Security-Policy"] = IMAGE_CSP
    return Response(content=data, media_type=document.mime_type, headers=headers)


@router.post(
    "/expenses/{expense_id}/documents",
    response_model=UploadResult,
    status_code=status.HTTP_201_CREATED,
)
def upload_to_expense(
    expense_id: uuid.UUID,
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    kind: DocumentKind = Form(default=DocumentKind.INVOICE_PHOTO),
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> UploadResult:
    try:
        expense = expense_service.get_expense(db, admin.company_id, expense_id)
    except expense_service.ExpenseNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Gasto no encontrado")
    actor = Actor.from_admin(admin, client_ip(request))
    data = read_upload(file)
    stored = handle_file_errors(
        lambda: service.store_document(
            db, storage, company_id=admin.company_id, data=data, kind=kind, actor=actor,
            employee_id=expense.employee_id, original_filename=file.filename,
        )
    )
    document = stored.document
    if document.expense_id not in (None, expense.id):
        # La misma foto ya soporta OTRO gasto: casi seguro es una factura duplicada.
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            {"message": "Este archivo ya está asociado a otro gasto",
             "existing_expense_id": str(document.expense_id)},
        )
    service.attach_to_expense(db, document, expense, actor)
    db.commit()
    if not stored.created:
        response.status_code = status.HTTP_200_OK
    return UploadResult(document=DocumentOut.from_model(document), duplicate=not stored.created)
