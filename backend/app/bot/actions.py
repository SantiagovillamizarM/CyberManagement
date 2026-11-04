"""Acciones que Python le pide ejecutar a n8n.

Contrato: cada acción es {"method": <método de la Bot API>, "payload": {...}}.
n8n hace POST https://api.telegram.org/bot<TOKEN>/<method> con el payload, sin
interpretarlo. Así toda la lógica vive en Python y n8n solo transporta.
"""

from typing import Any

Action = dict[str, Any]
Keyboard = list[list[tuple[str, str]]]  # filas de botones (texto, callback_data)


def send_message(
    chat_id: int,
    text: str,
    buttons: Keyboard | None = None,
    *,
    reply_keyboard: list[list[dict[str, Any]]] | None = None,
    remove_keyboard: bool = False,
) -> Action:
    """Envía un mensaje.

    - `buttons`: botones dentro del mensaje (inline).
    - `reply_keyboard`: teclado bajo la caja de texto. Es la única forma de pedir la
      ubicación (botón con "request_location"); los botones inline no pueden.
    - `remove_keyboard`: oculta un teclado de respuesta mostrado antes.
    """
    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": label, "callback_data": data} for label, data in row] for row in buttons
            ]
        }
    elif reply_keyboard:
        payload["reply_markup"] = {
            "keyboard": reply_keyboard,
            "resize_keyboard": True,
            "one_time_keyboard": True,
        }
    elif remove_keyboard:
        payload["reply_markup"] = {"remove_keyboard": True}
    return {"method": "sendMessage", "payload": payload}


def store_file(*, file_id: str, file_unique_id: str, telegram_user_id: int, purpose: str,
               file_name: str | None) -> Action:
    """Acción de TRANSPORTE (no es de Telegram): n8n descarga el archivo con getFile y
    lo sube a POST /api/internal/telegram/files. Python decide qué hacer al recibirlo."""
    return {
        "method": "cm:store_file",
        "payload": {
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "telegram_user_id": telegram_user_id,
            "purpose": purpose,  # "invoice" o "rut"
            "file_name": file_name or "",
        },
    }


def answer_callback(callback_id: str) -> Action:
    """Quita el "relojito" de carga del botón que el usuario pulsó."""
    return {"method": "answerCallbackQuery", "payload": {"callback_query_id": callback_id}}


def remove_buttons(chat_id: int, message_id: int) -> Action:
    """Quita los botones de un mensaje ya respondido, para que no se pulsen dos veces."""
    return {
        "method": "editMessageReplyMarkup",
        "payload": {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
    }
