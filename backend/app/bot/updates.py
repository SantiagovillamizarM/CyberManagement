"""Traduce un "update" crudo de Telegram a un objeto simple y predecible."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Kind(StrEnum):
    COMMAND = "command"
    TEXT = "text"
    CALLBACK = "callback"
    PHOTO = "photo"
    DOCUMENT = "document"
    LOCATION = "location"
    OTHER = "other"


@dataclass(frozen=True)
class Incoming:
    update_id: int
    user_id: int
    chat_id: int
    kind: Kind
    username: str | None = None
    first_name: str | None = None
    text: str | None = None
    command: str | None = None  # "start", "cancelar"... sin "/" ni "@nombre_del_bot"
    callback_id: str | None = None
    callback_data: str | None = None
    message_id: int | None = None  # mensaje con los botones pulsados
    latitude: float | None = None
    longitude: float | None = None


def parse_update(raw: dict[str, Any]) -> Incoming | None:
    """Devuelve None para lo que el bot ignora (grupos, canales, ediciones...)."""
    update_id = raw.get("update_id")
    if not isinstance(update_id, int):
        return None

    if "callback_query" in raw:
        query = raw["callback_query"]
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return None
        sender = query.get("from") or {}
        return Incoming(
            update_id=update_id,
            user_id=sender["id"],
            chat_id=chat["id"],
            kind=Kind.CALLBACK,
            username=sender.get("username"),
            first_name=sender.get("first_name"),
            callback_id=query.get("id"),
            callback_data=query.get("data"),
            message_id=message.get("message_id"),
        )

    message = raw.get("message")
    if not message:
        return None
    chat = message.get("chat") or {}
    sender = message.get("from") or {}
    # Solo chats privados: el bot no debe procesar datos financieros en grupos.
    if chat.get("type") != "private" or "id" not in sender:
        return None

    base = {
        "update_id": update_id,
        "user_id": sender["id"],
        "chat_id": chat["id"],
        "username": sender.get("username"),
        "first_name": sender.get("first_name"),
    }
    text = message.get("text")
    if text is not None:
        stripped = text.strip()
        if stripped.startswith("/"):
            command = stripped[1:].split()[0].split("@")[0].lower() if len(stripped) > 1 else ""
            return Incoming(**base, kind=Kind.COMMAND, text=stripped, command=command)
        return Incoming(**base, kind=Kind.TEXT, text=stripped)
    if "photo" in message:
        return Incoming(**base, kind=Kind.PHOTO)
    if "document" in message:
        return Incoming(**base, kind=Kind.DOCUMENT)
    if "location" in message:
        location = message["location"]
        return Incoming(
            **base,
            kind=Kind.LOCATION,
            latitude=location.get("latitude"),
            longitude=location.get("longitude"),
        )
    return Incoming(**base, kind=Kind.OTHER)
