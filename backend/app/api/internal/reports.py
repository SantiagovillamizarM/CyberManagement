"""Informe mensual automático (WF7 = CM-07 en n8n), protegido con X-Internal-Token.

    CM-07 (día 1, 7:00 a. m.) ──► POST /reports/monthly     Python genera el del mes anterior
                              ──► GET  /reports/{id}/file    n8n descarga el PDF
                              ──► Telegram sendDocument      al chat del administrador
                              ──► POST /reports/{id}/delivery n8n informa si llegó

Python decide qué incluir y a quién va; n8n solo programa, descarga y entrega.
"""

import html
import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.core.config import get_settings
from app.db.session import get_db
from app.models import Company, Report
from app.services import reports as service
from app.storage import Storage, get_storage
from app.storage.base import StorageError
from app.utils.dates import local_today
from app.utils.money import format_cop

router = APIRouter(prefix="/reports", tags=["interno: informes"], dependencies=[Depends(require_internal_token)])


class MonthlyDelivery(BaseModel):
    report_id: uuid.UUID
    chat_id: int
    caption: str  # HTML de Telegram, ya escapado
    file_path: str  # dónde descargar el PDF (interno)
    already_sent: bool  # True = ya se había enviado: n8n no lo vuelve a mandar


class MonthlyResponse(BaseModel):
    status: str  # "ready" o "skipped"
    reason: str | None = None
    month: str
    deliveries: list[MonthlyDelivery] = []


class DeliveryResult(BaseModel):
    ok: bool
    error_code: int | None = None
    description: str | None = Field(default=None, max_length=1000)


def _caption(company: Company, report: Report, month: date) -> str:
    s = report.summary or {}
    total = Decimal(s.get("total") or "0")
    remaining = Decimal(s.get("viaticos_remaining") or "0")
    return (f"📊 <b>Informe de gastos de {service.month_label(month)}</b>\n"
            f"{html.escape(company.name)}\n\n"
            f"Facturas: <b>{s.get('expenses', 0)}</b> · Total: <b>{format_cop(total)}</b>\n"
            f"Saldo de viáticos: <b>{format_cop(remaining)}</b>")


@router.post("/monthly", response_model=MonthlyResponse)
def monthly(
    month: str | None = Query(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
                              description="AAAA-MM; por defecto, el mes anterior"),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> MonthlyResponse:
    first = date.fromisoformat(f"{month}-01") if month else service.previous_month(local_today())
    key = first.strftime("%Y-%m")
    chat = get_settings().admin_telegram_chat_id
    if chat is None:
        # Sin destinatario no tiene sentido generarlo: se informa y n8n termina sin error.
        return MonthlyResponse(status="skipped", month=key,
                               reason="ADMIN_TELEGRAM_CHAT_ID no está configurado en el .env")
    deliveries = []
    for company in db.scalars(select(Company)):
        report = service.monthly_report(db, storage, company.id, first)
        deliveries.append(MonthlyDelivery(
            report_id=report.id, chat_id=chat, caption=_caption(company, report, first),
            file_path=f"/api/internal/reports/{report.id}/file",
            already_sent=bool((report.summary or {}).get("telegram_sent_at")),
        ))
    db.commit()
    return MonthlyResponse(status="ready", month=key, deliveries=deliveries)


def _load(db: Session, report_id: uuid.UUID) -> Report:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Informe no encontrado")
    return report


@router.get("/{report_id}/file")
def report_file(report_id: uuid.UUID, db: Session = Depends(get_db),
                storage: Storage = Depends(get_storage)) -> Response:
    report = _load(db, report_id)
    try:
        data = storage.read(report.storage_key)
    except StorageError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El archivo del informe no está disponible")
    name = f"informe-{report.filters.get('monthly') or report.filters['date_from']}.pdf"
    return Response(content=data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.post("/{report_id}/delivery")
def delivery(report_id: uuid.UUID, body: DeliveryResult, db: Session = Depends(get_db)) -> dict:
    report = _load(db, report_id)
    service.mark_delivery(db, report, ok=body.ok,
                          error=None if body.ok else f"{body.error_code or ''} {body.description or ''}".strip())
    db.commit()
    return {"id": str(report.id), "sent": body.ok}
