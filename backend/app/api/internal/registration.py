"""Endpoint interno que usa n8n (Workflow 1) para vincular Telegram con un código.

No es accesible desde el navegador: exige la cabecera X-Internal-Token.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.db.session import get_db
from app.services import registration
from app.services.registration import RedeemStatus

router = APIRouter(
    prefix="/registration",
    tags=["interno: registro"],
    dependencies=[Depends(require_internal_token)],
)


class RedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=64)
    telegram_user_id: int
    telegram_chat_id: int
    telegram_username: str | None = Field(default=None, max_length=64)


class RedeemResponse(BaseModel):
    status: RedeemStatus
    employee_first_name: str | None = None


@router.post("/redeem", response_model=RedeemResponse)
def redeem(body: RedeemRequest, db: Session = Depends(get_db)) -> RedeemResponse:
    result = registration.redeem_code(
        db,
        body.code,
        telegram_user_id=body.telegram_user_id,
        telegram_chat_id=body.telegram_chat_id,
        telegram_username=body.telegram_username,
    )
    db.commit()  # también en los fallos: así cuentan los intentos
    first_name = result.employee.first_name if result.employee else None
    return RedeemResponse(status=result.status, employee_first_name=first_name)
