"""Endpoint interno que llama n8n con cada update que llega de Telegram (Workflow 2).

Doble verificación:
- X-Internal-Token: prueba que quien llama es n8n.
- X-Telegram-Secret: el secret_token que Telegram envió a n8n; prueba que el
  update viene de Telegram y no de alguien que descubrió la URL del webhook.
"""

import hmac
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.bot.engine import handle_update
from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter(
    prefix="/telegram",
    tags=["interno: telegram"],
    dependencies=[Depends(require_internal_token)],
)


class ActionsResponse(BaseModel):
    actions: list[dict[str, Any]]


def require_telegram_secret(x_telegram_secret: str = Header(default="")) -> None:
    expected = get_settings().telegram_webhook_secret.get_secret_value()
    if not hmac.compare_digest(x_telegram_secret.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Secreto de Telegram inválido")


@router.post("/updates", response_model=ActionsResponse, dependencies=[Depends(require_telegram_secret)])
def process_update(update: dict[str, Any] = Body(...), db: Session = Depends(get_db)) -> ActionsResponse:
    actions = handle_update(db, update)
    db.commit()
    return ActionsResponse(actions=actions)
