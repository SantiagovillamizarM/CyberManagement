"""Lectura y validación de lo que escribe o envía el empleado.

Lo comparten el registro de gastos (expense_flow) y la corrección (fix_flow),
para que ambos apliquen exactamente las mismas reglas.
"""

from decimal import Decimal

from app.bot import messages as msg
from app.bot.actions import Action, send_message, store_file
from app.bot.updates import Incoming, Kind
from app.core.config import get_settings
from app.utils.money import InvalidAmountError, parse_cop

# Tipos de archivo aceptados (la validación definitiva se hace sobre el contenido descargado).
ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp", "application/pdf"}
INVOICE, RUT_FILE = "invoice", "rut"  # propósito de un archivo
CB_SKIP = "exp:skip"


class InvalidInput(Exception):
    """Respuesta inválida para el paso actual. El mensaje se muestra al empleado."""

    def __init__(self, message: str = msg.EXP_USE_LATEST) -> None:
        super().__init__(message)
        self.message = message


def text(incoming: Incoming, *, min_len: int, max_len: int) -> str:
    if incoming.kind != Kind.TEXT or not incoming.text:
        raise InvalidInput()
    value = incoming.text.strip()
    if not min_len <= len(value) <= max_len:
        raise InvalidInput(msg.EXP_BAD_TEXT.format(min=min_len, max=max_len))
    return value


def skipped(incoming: Incoming) -> bool:
    return incoming.callback_data == CB_SKIP or (
        incoming.kind == Kind.TEXT and (incoming.text or "").strip().lower() in {"omitir", msg.SKIP.lower()}
    )


def amount(incoming: Incoming) -> Decimal:
    if incoming.kind != Kind.TEXT:
        raise InvalidInput()
    try:
        return parse_cop(incoming.text or "")
    except InvalidAmountError:
        raise InvalidInput(msg.EXP_BAD_AMOUNT)


def request_file(incoming: Incoming, purpose: str) -> list[Action]:
    """Pide a n8n que descargue el archivo. Se rechaza ANTES de descargar si es muy grande o de otro tipo."""
    file = incoming.file
    max_mb = get_settings().max_upload_mb
    if file.file_size and file.file_size > max_mb * 1024 * 1024:
        raise InvalidInput(msg.EXP_FILE_TOO_LARGE.format(mb=max_mb))
    if incoming.kind == Kind.DOCUMENT and (file.mime_type or "") not in ALLOWED_MIME:
        raise InvalidInput(msg.EXP_FILE_TYPE)
    return [
        send_message(incoming.chat_id, msg.EXP_RECEIVING),
        store_file(file_id=file.file_id, file_unique_id=file.file_unique_id,
                   telegram_user_id=incoming.user_id, purpose=purpose, file_name=file.file_name),
    ]
