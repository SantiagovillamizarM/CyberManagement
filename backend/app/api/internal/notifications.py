"""Endpoints internos de avisos (los llama n8n, protegidos con X-Internal-Token).

- POST /notifications/claim      CM-06 cada minuto: toma los avisos listos y los envía a Telegram.
- POST /notifications/{id}/result CM-06 informa si Telegram lo entregó.
- POST /reminders/run             CM-08 una vez al día: crea los recordatorios.
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.db.session import get_db
from app.services import notifications as service

router = APIRouter(tags=["interno: avisos"], dependencies=[Depends(require_internal_token)])


class ClaimedNotification(BaseModel):
    id: uuid.UUID
    attempt: int
    action: dict[str, Any]  # mismo formato que las acciones del bot: {"method", "payload"}


class ClaimResponse(BaseModel):
    notifications: list[ClaimedNotification]


class DeliveryResult(BaseModel):
    ok: bool
    # Lo que respondió Telegram cuando falló (error_code 403 = el empleado bloqueó el bot).
    error_code: int | None = None
    description: str | None = Field(default=None, max_length=1000)


class ResultResponse(BaseModel):
    id: uuid.UUID
    status: str
    attempts: int


@router.post("/notifications/claim", response_model=ClaimResponse)
def claim(limit: int = Query(default=20, ge=1, le=100), db: Session = Depends(get_db)) -> ClaimResponse:
    claimed = service.claim_due(db, limit)
    db.commit()
    return ClaimResponse(notifications=[
        ClaimedNotification(id=n.id, attempt=n.attempts, action=service.as_action(n)) for n in claimed
    ])


@router.post("/notifications/{notification_id}/result", response_model=ResultResponse)
def result(notification_id: uuid.UUID, body: DeliveryResult, db: Session = Depends(get_db)) -> ResultResponse:
    try:
        notification = service.report_result(db, notification_id, ok=body.ok, error_code=body.error_code,
                                             description=body.description)
    except service.NotificationNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Aviso no encontrado")
    db.commit()
    return ResultResponse(id=notification.id, status=notification.status.value, attempts=notification.attempts)


@router.post("/reminders/run")
def run_reminders(db: Session = Depends(get_db)) -> dict[str, int]:
    """Idempotente: ejecutarlo varias veces el mismo día no repite recordatorios."""
    counts = service.run_reminders(db)
    db.commit()
    return counts
