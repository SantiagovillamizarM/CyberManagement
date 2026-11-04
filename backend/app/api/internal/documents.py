"""Endpoint interno: n8n sube aquí las fotos que llegan por Telegram (Workflow 2).

La foto se guarda de inmediato, antes de cualquier análisis con IA.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.api.uploads import handle_file_errors, read_upload
from app.db.session import get_db
from app.models.enums import DocumentKind, EmployeeStatus
from app.schemas.documents import DocumentOut
from app.services import documents as service
from app.services.audit import Actor
from app.storage import Storage, get_storage

router = APIRouter(
    prefix="/documents",
    tags=["interno: documentos"],
    dependencies=[Depends(require_internal_token)],
)


class InternalUploadResult(BaseModel):
    document: DocumentOut
    duplicate: bool


@router.post("", response_model=InternalUploadResult, status_code=status.HTTP_201_CREATED)
def upload_from_telegram(
    response: Response,
    file: UploadFile = File(...),
    telegram_user_id: int = Form(...),
    telegram_file_unique_id: str | None = Form(default=None, max_length=100),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> InternalUploadResult:
    employee = service.employee_by_telegram(db, telegram_user_id)
    if employee is None or employee.status != EmployeeStatus.ACTIVE or employee.deleted_at:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "La cuenta de Telegram no está vinculada a un empleado activo")

    data = read_upload(file)
    stored = handle_file_errors(
        lambda: service.store_document(
            db, storage, company_id=employee.company_id, data=data, kind=DocumentKind.INVOICE_PHOTO,
            actor=Actor.from_employee(employee), employee_id=employee.id,
            original_filename=file.filename, telegram_file_unique_id=telegram_file_unique_id,
        )
    )
    db.commit()
    if not stored.created:
        response.status_code = status.HTTP_200_OK
    return InternalUploadResult(document=DocumentOut.from_model(stored.document), duplicate=not stored.created)
