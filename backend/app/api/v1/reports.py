"""Informes PDF desde el panel: generar, listar, ver (vista previa) y descargar."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.api.deps import READERS, client_ip, require_roles
from app.db.session import get_db
from app.models import AdminUser, Report
from app.schemas.reports import ReportOut, ReportRequest
from app.services import reports as service
from app.services.audit import Actor
from app.storage import Storage, get_storage
from app.storage.base import StorageError

router = APIRouter(prefix="/reports", tags=["informes"])


def _to_out(db: Session, report: Report) -> ReportOut:
    author = db.get(AdminUser, report.generated_by_id) if report.generated_by_id else None
    return ReportOut(
        id=report.id, filters=report.filters, summary=report.summary, size_bytes=report.size_bytes,
        generated_by=author.full_name if author else "Envío automático mensual",
        created_at=report.created_at, file_url=f"/api/v1/reports/{report.id}/file",
    )


@router.post("", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    body: ReportRequest,
    request: Request,
    admin: AdminUser = Depends(require_roles(*READERS)),  # el auditor también genera informes (solo lectura)
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> ReportOut:
    filters = service.ReportFilters(
        date_from=body.date_from, date_to=body.date_to, employee_id=body.employee_id,
        category_id=body.category_id, statuses=tuple(dict.fromkeys(body.statuses)), include_photos=body.include_photos,
    )
    try:
        report, _ = service.generate(db, storage, admin, filters, Actor.from_admin(admin, client_ip(request)))
    except service.ReportRuleError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    db.commit()
    return _to_out(db, report)


@router.get("", response_model=list[ReportOut])
def list_reports(
    limit: int = Query(default=30, ge=1, le=100),
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
) -> list[ReportOut]:
    return [_to_out(db, r) for r in service.list_reports(db, admin.company_id, limit)]


@router.get("/{report_id}/file")
def report_file(
    report_id: uuid.UUID,
    download: bool = False,
    admin: AdminUser = Depends(require_roles(*READERS)),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> Response:
    """El PDF. Por defecto se muestra en el navegador (vista previa); con ?download=true se descarga."""
    try:
        report = service.get_report(db, admin.company_id, report_id)
        data = storage.read(report.storage_key)
    except (service.ReportNotFoundError, StorageError):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Informe no encontrado")
    name = f"informe-{report.filters['date_from']}-a-{report.filters['date_to']}.pdf"
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{name}"',
        # La vista previa vive en un iframe del panel (mismo sitio). Ningún otro sitio puede incrustarlo.
        "X-Frame-Options": "SAMEORIGIN",
        "Content-Security-Policy": "frame-ancestors 'self'",
    })
