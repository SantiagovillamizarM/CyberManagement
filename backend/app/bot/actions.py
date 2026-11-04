"""Acciones que Python le pide ejecutar a n8n.

Contrato: cada acción es {"method": <método de la Bot API>, "payload": {...}}.
n8n hace POST https://api.telegram.org/bot<TOKEN>/<method> con el payload, sin
interpretarlo. Así toda la lógica vive en Python y n8n solo transporta.
"""

from typing import Any

Action = dict[str, Any]
Keyboard = list[list[tuple[str, str]]]  # filas de botones (texto, callback_data)


def send_message(chat_id: int, text: str, buttons: Keyboard | None = None) -> Action:
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
    return {"method": "sendMessage", "payload": payload}


def answer_callback(callback_id: str) -> Action:
    """Quita el "relojito" de carga del botón que el usuario pulsó."""
    return {"method": "answerCallbackQuery", "payload": {"callback_query_id": callback_id}}


def remove_buttons(chat_id: int, message_id: int) -> Action:
    """Quita los botones de un mensaje ya respondido, para que no se pulsen dos veces."""
    return {
        "method": "editMessageReplyMarkup",
        "payload": {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
    }
