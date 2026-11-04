"""Paso 4.1: motor de conversación del bot y endpoint interno de Telegram."""

import itertools
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import engine
from app.bot import messages as msg
from app.core.config import get_settings
from app.models import AdminUser, BotSession, DataConsent, Employee, TelegramEvent
from app.models.enums import EmployeeStatus, MovementSource
from app.services import registration, viaticos
from app.services.audit import Actor
from app.utils.money import format_cop

USER = 70001
_update_ids = itertools.count(1000)


# --- Simulador de updates de Telegram (mismo formato JSON que envía Telegram) ---


def _sender(user_id: int) -> dict:
    return {"id": user_id, "is_bot": False, "first_name": "Jhon", "username": "jhon_tg"}


def text_update(text: str, user_id: int = USER, chat_type: str = "private") -> dict:
    return {
        "update_id": next(_update_ids),
        "message": {
            "message_id": 1,
            "from": _sender(user_id),
            "chat": {"id": user_id, "type": chat_type},
            "date": 1762200000,
            "text": text,
        },
    }


def button_update(data: str, user_id: int = USER) -> dict:
    return {
        "update_id": next(_update_ids),
        "callback_query": {
            "id": "cbq-1",
            "from": _sender(user_id),
            "message": {"message_id": 55, "chat": {"id": user_id, "type": "private"}},
            "data": data,
        },
    }


def photo_update(user_id: int = USER) -> dict:
    update = text_update("x", user_id)
    del update["message"]["text"]
    update["message"]["photo"] = [{"file_id": "abc", "file_unique_id": "u1", "width": 90, "height": 90}]
    return update


def texts(actions: list[dict]) -> list[str]:
    return [a["payload"]["text"] for a in actions if a["method"] == "sendMessage"]


def buttons(actions: list[dict]) -> list[str]:
    return [
        b["callback_data"]
        for a in actions
        if a["method"] == "sendMessage" and "reply_markup" in a["payload"]
        for row in a["payload"]["reply_markup"]["inline_keyboard"]
        for b in row
    ]


@pytest.fixture
def code(db: Session, employee: Employee, admin: AdminUser) -> str:
    return registration.generate_code(db, employee, Actor.from_admin(admin)).code


def send(db: Session, update: dict) -> list[dict]:
    return engine.handle_update(db, update)


def link(db: Session, code: str) -> list[dict]:
    send(db, text_update("/start"))
    send(db, button_update(engine.CONSENT_YES))
    return send(db, text_update(code))


# --- CA-4: bienvenida y autorización ANTES del código ---


def test_ca4_new_user_gets_welcome_and_consent_first(db: Session) -> None:
    actions = send(db, text_update("/start"))

    assert texts(actions)[0] == msg.WELCOME
    assert "Ley 1581" in texts(actions)[1]
    assert buttons(actions) == [engine.CONSENT_YES, engine.CONSENT_NO]
    assert db.get(BotSession, USER).state == engine.AWAITING_CONSENT


def test_ca4_code_is_not_accepted_before_consent(db: Session, code: str, employee: Employee) -> None:
    send(db, text_update("/start"))

    actions = send(db, text_update(code))  # intenta saltarse la autorización

    assert texts(actions) == [msg.CONSENT_USE_BUTTONS]
    assert employee.telegram_user_id is None


def test_ca4_declining_registers_nothing(db: Session, code: str, employee: Employee) -> None:
    send(db, text_update("/start"))

    actions = send(db, button_update(engine.CONSENT_NO))

    assert msg.CONSENT_DECLINED in texts(actions)
    assert db.get(BotSession, USER).state == engine.IDLE
    assert employee.telegram_user_id is None
    assert db.scalars(select(DataConsent)).all() == []


def test_pressed_buttons_are_answered_and_removed(db: Session) -> None:
    send(db, text_update("/start"))

    actions = send(db, button_update(engine.CONSENT_YES))

    assert actions[0] == {"method": "answerCallbackQuery", "payload": {"callback_query_id": "cbq-1"}}
    assert actions[1]["method"] == "editMessageReplyMarkup"
    assert texts(actions) == [msg.ASK_CODE]


# --- CA-5: vinculación exitosa con autorización guardada ---


def test_ca5_valid_code_links_and_stores_consent(db: Session, code: str, employee: Employee) -> None:
    actions = link(db, code)

    assert texts(actions)[0] == msg.REGISTER_SUCCESS.format(first_name="Jhon")
    assert "¡Registro exitoso!" in texts(actions)[0]
    assert engine.CB_EXPENSE in buttons(actions)  # muestra el menú
    assert employee.telegram_user_id == USER
    assert employee.telegram_username == "jhon_tg"
    consent = db.scalars(select(DataConsent)).one()
    assert consent.employee_id == employee.id
    assert consent.policy_version == get_settings().data_policy_version
    assert consent.channel == "telegram"


# --- CA-6: errores claros y reintento ---


def test_ca6_invalid_code_then_retry(db: Session, code: str, employee: Employee) -> None:
    send(db, text_update("/start"))
    send(db, button_update(engine.CONSENT_YES))

    assert texts(send(db, text_update("JHON-0000-0000"))) == [msg.CODE_INVALID]
    assert db.get(BotSession, USER).state == engine.AWAITING_CODE  # puede reintentar

    assert "¡Registro exitoso!" in texts(send(db, text_update(code)))[0]


def test_ca6_too_many_attempts(db: Session, code: str) -> None:
    send(db, text_update("/start"))
    send(db, button_update(engine.CONSENT_YES))
    for _ in range(5):
        send(db, text_update("JHON-0000-0000"))

    assert texts(send(db, text_update(code))) == [msg.CODE_TOO_MANY]


def test_ca6_inactive_employee_code(db: Session, code: str, employee: Employee) -> None:
    employee.status = EmployeeStatus.INACTIVE

    assert texts(link(db, code)) == [msg.EMPLOYEE_INACTIVE]


def test_ca6_photo_instead_of_code(db: Session) -> None:
    send(db, text_update("/start"))
    send(db, button_update(engine.CONSENT_YES))

    assert texts(send(db, photo_update())) == [msg.CODE_NEEDS_TEXT]


# --- CA-7: menú y cuenta desactivada ---


def test_ca7_linked_user_sees_menu(db: Session, code: str) -> None:
    link(db, code)

    actions = send(db, text_update("hola"))

    assert texts(actions) == [msg.MENU.format(first_name="Jhon")]
    assert buttons(actions) == [engine.CB_EXPENSE, engine.CB_BALANCE, engine.CB_EXPENSES, engine.CB_HELP]


def test_ca7_deactivated_employee_is_blocked(db: Session, code: str, employee: Employee) -> None:
    link(db, code)
    employee.status = EmployeeStatus.INACTIVE

    assert texts(send(db, text_update("/menu"))) == [msg.EMPLOYEE_INACTIVE]


def test_balance_shows_open_viaticos(db: Session, code: str, employee: Employee, admin) -> None:
    link(db, code)
    viaticos.open_viatico(db, employee, initial_amount=Decimal("500000"), purpose="Viaje a Medellín",
                          source=MovementSource.ADMIN, actor=Actor.from_admin(admin))

    balance = texts(send(db, button_update(engine.CB_BALANCE)))[0]

    assert "Viaje a Medellín" in balance
    assert "$500.000" in balance


def test_balance_without_viaticos(db: Session, code: str) -> None:
    link(db, code)

    assert texts(send(db, text_update("/saldo")))[0] == msg.NO_OPEN_VIATICOS


def test_user_input_is_html_escaped(db: Session, employee: Employee, admin) -> None:
    employee.first_name = "<b>Jhon</b>"
    issued = registration.generate_code(db, employee, Actor.from_admin(admin))

    success = texts(link(db, issued.code))[0]

    assert "&lt;b&gt;Jhon&lt;/b&gt;" in success


# --- CA-8: /cancelar y /ayuda ---


def test_ca8_cancel_at_any_step(db: Session) -> None:
    send(db, text_update("/start"))
    send(db, button_update(engine.CONSENT_YES))

    assert texts(send(db, text_update("/cancelar"))) == [msg.CANCELLED_UNLINKED]
    assert db.get(BotSession, USER).state == engine.IDLE


def test_ca8_help(db: Session, code: str) -> None:
    assert texts(send(db, text_update("/ayuda"))) == [msg.HELP_UNLINKED]
    link(db, code)
    # La ayuda va seguida del menú, para que el empleado no quede sin opciones.
    assert texts(send(db, text_update("/ayuda@CO_CyberManagement_bot"))) == [
        msg.HELP_LINKED,
        msg.MENU.format(first_name="Jhon"),
    ]


# --- CA-3: idempotencia ---


def test_ca3_same_update_is_processed_once(db: Session) -> None:
    update = text_update("/start")

    first = send(db, update)
    again = send(db, update)  # Telegram reintentó el envío

    assert len(texts(first)) == 2
    assert again == []
    assert len(db.scalars(select(TelegramEvent)).all()) == 1


def test_group_chats_are_ignored(db: Session) -> None:
    assert send(db, text_update("/start", chat_type="group")) == []
    assert db.get(BotSession, USER) is None


def test_garbage_is_ignored(db: Session) -> None:
    assert send(db, {"foo": "bar"}) == []
    assert send(db, {"update_id": 1, "edited_message": {}}) == []


# --- CA-2: endpoint interno ---


def _headers(internal: str | None = None, secret: str | None = None) -> dict:
    s = get_settings()
    return {
        "X-Internal-Token": internal if internal is not None else s.internal_api_token.get_secret_value(),
        "X-Telegram-Secret": secret if secret is not None else s.telegram_webhook_secret.get_secret_value(),
    }


@pytest.mark.parametrize("headers", [_headers(internal="x"), _headers(secret="x"), {}])
def test_ca2_endpoint_rejects_missing_or_wrong_secrets(client: TestClient, headers: dict) -> None:
    response = client.post("/api/internal/telegram/updates", json=text_update("/start"), headers=headers)

    assert response.status_code == 401


def test_ca2_endpoint_returns_actions(client: TestClient) -> None:
    response = client.post("/api/internal/telegram/updates", json=text_update("/start"), headers=_headers())

    assert response.status_code == 200
    actions = response.json()["actions"]
    assert [a["method"] for a in actions] == ["sendMessage", "sendMessage"]
    assert actions[0]["payload"]["chat_id"] == USER


# --- Formato de dinero ---


@pytest.mark.parametrize(
    ("value", "expected"),
    [("45000", "$45.000"), ("1234567.5", "$1.234.567,50"), ("-120000", "-$120.000"), ("0", "$0")],
)
def test_format_cop(value: str, expected: str) -> None:
    assert format_cop(Decimal(value)) == expected
