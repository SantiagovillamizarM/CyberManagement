"""Motor de conversación del bot: recibe un update y devuelve las acciones a ejecutar.

Máquina de estados (bot_sessions.state):

    Usuario NO vinculado:
        idle ──(cualquier mensaje)──► awaiting_consent ──(Acepto)──► awaiting_code
                                           └──(No acepto)──► idle
        awaiting_code ──(código válido)──► menu   [se vincula y se guarda la autorización]

    Empleado vinculado:
        menu  (en el paso 4.2 se agregan los estados del registro de gastos)
"""

import html
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bot import messages as msg
from app.bot.actions import Action, Keyboard, answer_callback, remove_buttons, send_message
from app.bot.updates import Incoming, Kind, parse_update
from app.core.config import get_settings
from app.models import BotSession, DataConsent, Employee, Expense, TelegramEvent
from app.models.enums import EmployeeStatus, ViaticoStatus
from app.services import registration, viaticos
from app.services.registration import RedeemStatus
from app.utils.money import format_cop

logger = logging.getLogger(__name__)

# Estados
IDLE = "idle"
AWAITING_CONSENT = "awaiting_consent"
AWAITING_CODE = "awaiting_code"
MENU = "menu"

# Datos de los botones (máximo 64 bytes cada uno, lo exige Telegram)
CONSENT_YES, CONSENT_NO = "consent:yes", "consent:no"
CB_EXPENSE, CB_BALANCE, CB_EXPENSES, CB_HELP = "menu:expense", "menu:balance", "menu:expenses", "menu:help"

MENU_BUTTONS: Keyboard = [
    [(msg.MENU_EXPENSE, CB_EXPENSE)],
    [(msg.MENU_BALANCE, CB_BALANCE), (msg.MENU_EXPENSES, CB_EXPENSES)],
    [(msg.MENU_HELP, CB_HELP)],
]


def handle_update(db: Session, raw: dict) -> list[Action]:
    """Punto de entrada. El llamador debe hacer commit al terminar."""
    incoming = parse_update(raw)
    if incoming is None:
        return []

    employee = db.scalar(select(Employee).where(Employee.telegram_user_id == incoming.user_id))
    if not _register_event(db, incoming, employee):
        logger.info("Update %s repetido: se ignora", incoming.update_id)
        return []

    session = db.get(BotSession, incoming.user_id)
    if session is None:
        session = BotSession(telegram_user_id=incoming.user_id, state=IDLE, data={})
        db.add(session)

    actions: list[Action] = []
    if incoming.kind == Kind.CALLBACK:
        if incoming.callback_id:
            actions.append(answer_callback(incoming.callback_id))
        if incoming.message_id:
            actions.append(remove_buttons(incoming.chat_id, incoming.message_id))

    if employee is None:
        actions += _unlinked(db, session, incoming)
    elif employee.status != EmployeeStatus.ACTIVE or employee.deleted_at is not None:
        _reset(session, IDLE)
        actions.append(send_message(incoming.chat_id, msg.EMPLOYEE_INACTIVE))
    else:
        session.employee_id = employee.id
        actions += _linked(db, session, employee, incoming)
    db.flush()
    return actions


def _register_event(db: Session, incoming: Incoming, employee: Employee | None) -> bool:
    """Guarda el update. Devuelve False si ya se había procesado (Telegram reintenta envíos)."""
    if db.scalar(select(TelegramEvent.id).where(TelegramEvent.update_id == incoming.update_id)):
        return False
    try:
        with db.begin_nested():
            db.add(
                TelegramEvent(
                    update_id=incoming.update_id,
                    telegram_user_id=incoming.user_id,
                    employee_id=employee.id if employee else None,
                    event_type=incoming.kind.value,
                )
            )
            db.flush()
    except IntegrityError:  # llegó el mismo update al mismo tiempo por otra vía
        return False
    return True


def _reset(session: BotSession, state: str) -> None:
    session.state = state
    session.data = {}
    session.draft_expense_id = None


def _first_name(name: str | None) -> str:
    return html.escape(name or "")


# --- Usuario sin vincular ---


def _unlinked(db: Session, session: BotSession, incoming: Incoming) -> list[Action]:
    chat = incoming.chat_id
    if incoming.kind == Kind.COMMAND:
        if incoming.command == "ayuda":
            return [send_message(chat, msg.HELP_UNLINKED)]
        if incoming.command == "cancelar":
            _reset(session, IDLE)
            return [send_message(chat, msg.CANCELLED_UNLINKED)]
        if incoming.command == "start" and session.state == AWAITING_CODE:
            return [send_message(chat, msg.ASK_CODE)]

    if session.state == AWAITING_CONSENT:
        if incoming.callback_data == CONSENT_YES:
            session.state = AWAITING_CODE
            session.data = {
                "consent_version": get_settings().data_policy_version,
                "consent_at": datetime.now(UTC).isoformat(),
            }
            return [send_message(chat, msg.ASK_CODE)]
        if incoming.callback_data == CONSENT_NO:
            _reset(session, IDLE)
            return [send_message(chat, msg.CONSENT_DECLINED)]
        return [send_message(chat, msg.CONSENT_USE_BUTTONS, _consent_buttons())]

    if session.state == AWAITING_CODE:
        if incoming.kind != Kind.TEXT:
            return [send_message(chat, msg.CODE_NEEDS_TEXT)]
        return _redeem(db, session, incoming)

    # idle (o cualquier estado desconocido): empieza el registro
    session.state = AWAITING_CONSENT
    session.data = {}
    return [
        send_message(chat, msg.WELCOME),
        send_message(chat, msg.CONSENT_REQUEST, _consent_buttons()),
    ]


def _consent_buttons() -> Keyboard:
    return [[(msg.CONSENT_ACCEPT, CONSENT_YES), (msg.CONSENT_DECLINE, CONSENT_NO)]]


def _redeem(db: Session, session: BotSession, incoming: Incoming) -> list[Action]:
    chat = incoming.chat_id
    result = registration.redeem_code(
        db,
        incoming.text or "",
        telegram_user_id=incoming.user_id,
        telegram_chat_id=chat,
        telegram_username=incoming.username,
    )
    if result.status == RedeemStatus.LINKED:
        employee = result.employee
        consent = session.data or {}
        db.add(
            DataConsent(
                employee_id=employee.id,
                policy_version=consent.get("consent_version", get_settings().data_policy_version),
                channel="telegram",
                accepted_at=datetime.fromisoformat(consent["consent_at"])
                if consent.get("consent_at")
                else datetime.now(UTC),
            )
        )
        session.employee_id = employee.id
        _reset(session, MENU)
        name = _first_name(employee.first_name)
        return [
            send_message(chat, msg.REGISTER_SUCCESS.format(first_name=name)),
            send_message(chat, msg.MENU.format(first_name=name), MENU_BUTTONS),
        ]
    replies = {
        RedeemStatus.INVALID_CODE: msg.CODE_INVALID,
        RedeemStatus.TOO_MANY_ATTEMPTS: msg.CODE_TOO_MANY,
        RedeemStatus.EMPLOYEE_INACTIVE: msg.EMPLOYEE_INACTIVE,
    }
    return [send_message(chat, replies.get(result.status, msg.CODE_INVALID))]


# --- Empleado vinculado ---


def _linked(db: Session, session: BotSession, employee: Employee, incoming: Incoming) -> list[Action]:
    chat = incoming.chat_id
    name = _first_name(employee.first_name)
    command = incoming.command if incoming.kind == Kind.COMMAND else None
    choice = incoming.callback_data

    if command == "cancelar":
        _reset(session, MENU)
        return [send_message(chat, msg.CANCELLED), _menu(chat, name)]
    if command == "ayuda" or choice == CB_HELP:
        return [send_message(chat, msg.HELP_LINKED)]
    if command == "saldo" or choice == CB_BALANCE:
        return [send_message(chat, _balance_text(db, employee)), _menu(chat, name)]
    if command == "gastos" or choice == CB_EXPENSES:
        return [send_message(chat, _expenses_text(db, employee)), _menu(chat, name)]
    if choice == CB_EXPENSE:
        return [send_message(chat, msg.EXPENSE_COMING_SOON), _menu(chat, name)]

    # /start, /menu, texto libre o estado desconocido: menú principal
    _reset(session, MENU)
    return [_menu(chat, name)]


def _menu(chat_id: int, name: str) -> Action:
    return send_message(chat_id, msg.MENU.format(first_name=name), MENU_BUTTONS)


def _balance_text(db: Session, employee: Employee) -> str:
    open_viaticos = viaticos.list_viaticos(
        db, employee.company_id, employee_id=employee.id, status=ViaticoStatus.OPEN
    )
    if not open_viaticos:
        return msg.NO_OPEN_VIATICOS
    text = msg.BALANCE_HEADER
    for viatico in open_viaticos:
        balance = viaticos.compute_balance(db, viatico)
        text += msg.BALANCE_LINE.format(
            purpose=html.escape(viatico.purpose or "Viático"),
            delivered=format_cop(balance.delivered - balance.refunded),
            spent=format_cop(balance.spent),
            remaining=format_cop(balance.remaining),
        )
    return text


def _expenses_text(db: Session, employee: Employee, limit: int = 5) -> str:
    recent = db.scalars(
        select(Expense)
        .where(Expense.employee_id == employee.id, Expense.deleted_at.is_(None))
        .order_by(Expense.received_at.desc())
        .limit(limit)
    ).all()
    if not recent:
        return msg.NO_EXPENSES
    text = msg.EXPENSES_HEADER
    for expense in recent:
        when = expense.issue_date or expense.received_at.date()
        text += msg.EXPENSE_LINE.format(
            date=when.strftime("%d/%m/%Y"),
            description=html.escape((expense.description or "Sin descripción")[:40]),
            total=format_cop(expense.total),
            status=msg.EXPENSE_STATUS_LABELS.get(expense.status.value, expense.status.value),
        )
    return text
