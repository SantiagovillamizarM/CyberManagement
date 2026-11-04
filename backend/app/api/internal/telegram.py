"""Endpoint interno que llama n8n con cada update que llega de Telegram (Workflow 2).

Doble verificación:
- X-Internal-Token: prueba que quien llama es n8n.
- X-Telegram-Secret: el secret_token que Telegram envió a n8n; prueba que el
  update viene de Telegram y no de alguien que descubrió la URL del webhook.
"""

import hmac
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_internal_token
from app.api.uploads import read_upload
from app.bot.engine import handle_file, handle_update
from app.core.config import get_settings
from app.db.session import get_db
from app.storage import Storage, get_storage

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


@router.post("/files", response_model=ActionsResponse)
def receive_file(
    file: UploadFile = File(...),
    telegram_user_id: int = Form(...),
    purpose: Literal["invoice", "rut"] = Form(...),
    file_unique_id: str | None = Form(default=None, max_length=100),
    file_name: str | None = Form(default=None, max_length=255),
    db: Session = Depends(get_db),
    storage: Storage = Depends(get_storage),
) -> ActionsResponse:
    """n8n sube aquí el archivo que descargó de Telegram (acción cm:store_file).

    No exige el secreto de Telegram: esta llamada no viene de Telegram sino de n8n
    (protegida por X-Internal-Token), con un archivo que Python mismo pidió descargar.
    """
    actions = handle_file(
        db, storage,
        telegram_user_id=telegram_user_id, purpose=purpose, data=read_upload(file),
        file_name=file_name or file.filename, file_unique_id=file_unique_id,
    )
    db.commit()
    return ActionsResponse(actions=actions)
