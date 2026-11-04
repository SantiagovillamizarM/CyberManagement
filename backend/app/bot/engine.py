"""Motor de conversación del bot: recibe un update y devuelve las acciones a ejecutar.

Máquina de estados (bot_sessions.state):

    Usuario NO vinculado:
        idle ──(cualquier mensaje)──► awaiting_consent ──(Acepto)──► awaiting_code
                                           │  └──(otra cosa)──► repite la autorización
                                           └──(No acepto / /cancelar)──► consent_declined
        consent_declined ──(cualquier mensaje)──► awaiting_consent  [sin repetir la bienvenida]
        awaiting_code ──(código válido)──► menu   [se vincula y se guarda la autorización]

    Empleado vinculado:
        menu ──(📸 Registrar gasto)──► exp:*  (ver expense_flow.py) ──► menu
        menu ──(🧾 Mis gastos → un gasto)──► fix:*  (ver fix_flow.py) ──► menu
"""

import html
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bot import messages as msg
from app.bot import expense_flow, fix_flow
from app.bot.actions import Action, Keyboard, answer_callback, remove_buttons, send_message
from app.bot.menu import CB_BALANCE, CB_EXPENSE, CB_EXPENSES, CB_HELP, MENU, menu_action
from app.bot.updates import Incoming, Kind, parse_update
from app.core.config import get_settings
from app.models import BotSession, DataConsent, Employee, Expense, TelegramEvent
from app.models.enums import DocumentKind, EmployeeStatus, ViaticoStatus
from app.services import documents, registration, viaticos
from app.services.audit import Actor
from app.services.registration import RedeemStatus
from app.storage import Storage
from app.storage.inspection import FileTooLargeError, InvalidFileError, UnsupportedFileError
from app.utils.money import format_cop

logger = logging.getLogger(__name__)

# Estados
IDLE = "idle"
AWAITING_CONSENT = "awaiting_consent"
AWAITING_CODE = "awaiting_code"
CONSENT_DECLINED = "consent_declined"  # no aceptó (o canceló): conversación detenida

# Datos de los botones (máximo 64 bytes cada uno, lo exige Telegram)
CONSENT_YES, CONSENT_NO = "consent:yes", "consent:no"


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
    """La autorización de datos es un punto de parada: sin un "Acepto" explícito no se avanza."""
    chat = incoming.chat_id
    state = session.state

    # 1. Respuesta a la autorización. Vale también el botón de un mensaje anterior
    #    (CA-6): es una respuesta explícita a la misma política.
    if state in (AWAITING_CONSENT, CONSENT_DECLINED):
        if incoming.callback_data == CONSENT_YES:
            session.state = AWAITING_CODE
            session.data = {
                "consent_version": get_settings().data_policy_version,
                "consent_at": datetime.now(UTC).isoformat(),
            }
            return [send_message(chat, msg.ASK_CODE)]
        if incoming.callback_data == CONSENT_NO or (state == AWAITING_CONSENT and incoming.command == "cancelar"):
            return _stop(session, chat, msg.CONSENT_DECLINED)

    # 2. Comandos generales
    if incoming.command == "ayuda":
        actions = [send_message(chat, msg.HELP_UNLINKED)]
        if state == AWAITING_CONSENT:  # la ayuda no la saca de la autorización
            actions.append(_consent_request(chat))
        return actions
    if incoming.command == "cancelar":
        return _stop(session, chat, msg.CANCELLED_UNLINKED)

    # 3. Esperando el código (ya aceptó)
    if state == AWAITING_CODE:
        if incoming.command == "start":
            return [send_message(chat, msg.ASK_CODE)]
        if incoming.kind != Kind.TEXT:
            return [send_message(chat, msg.CODE_NEEDS_TEXT)]
        return _redeem(db, session, incoming)

    # 4. Cualquier otra cosa: (volver a) mostrar la autorización y esperar.
    session.state = AWAITING_CONSENT
    session.data = {}
    if state == AWAITING_CONSENT:  # respondió otra cosa
        return [send_message(chat, msg.CONSENT_PENDING), _consent_request(chat)]
    if state == CONSENT_DECLINED:  # ya conoce la bienvenida
        return [_consent_request(chat)]
    return [send_message(chat, msg.WELCOME), _consent_request(chat)]


def _stop(session: BotSession, chat: int, text: str) -> list[Action]:
    """Termina la conversación. El siguiente mensaje vuelve a la autorización."""
    _reset(session, CONSENT_DECLINED)
    return [send_message(chat, text)]


def _consent_request(chat: int) -> Action:
    buttons: Keyboard = [[(msg.CONSENT_ACCEPT, CONSENT_YES), (msg.CONSENT_DECLINE, CONSENT_NO)]]
    return send_message(chat, msg.CONSENT_REQUEST, buttons)


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
        return [send_message(chat, msg.REGISTER_SUCCESS.format(first_name=name)), menu_action(chat, employee)]
    replies = {
        RedeemStatus.INVALID_CODE: msg.CODE_INVALID,
        RedeemStatus.TOO_MANY_ATTEMPTS: msg.CODE_TOO_MANY,
        RedeemStatus.EMPLOYEE_INACTIVE: msg.EMPLOYEE_INACTIVE,
    }
    return [send_message(chat, replies.get(result.status, msg.CODE_INVALID))]


# --- Empleado vinculado ---


def _linked(db: Session, session: BotSession, employee: Employee, incoming: Incoming) -> list[Action]:
    chat = incoming.chat_id
    command = incoming.command if incoming.kind == Kind.COMMAND else None
    choice = incoming.callback_data
    in_expense = expense_flow.is_active(session)
    in_fix = fix_flow.is_active(session)

    if command == "cancelar":
        if in_expense:
            return expense_flow.cancel(session, employee, chat)
        _reset(session, MENU)
        return [send_message(chat, msg.CANCELLED), menu_action(chat, employee)]
    if command in {"start", "menu"}:
        if in_expense:
            return expense_flow.cancel(session, employee, chat)
        _reset(session, MENU)
        return [menu_action(chat, employee)]

    # Fuera de un registro, "Mis gastos" es una lista interactiva para consultar y corregir.
    if not in_expense and (command == "gastos" or choice == CB_EXPENSES):
        return fix_flow.list_expenses(db, session, employee, chat)

    # Consultas que no interrumpen un registro en curso: se responde y se repite la pregunta.
    info = None
    if command == "ayuda" or (choice == CB_HELP and not in_expense):
        info = msg.HELP_LINKED
    elif command == "saldo" or (choice == CB_BALANCE and not in_expense):
        info = _balance_text(db, employee)
    elif command == "gastos" or (choice == CB_EXPENSES and not in_expense):
        info = _expenses_text(db, employee)
    if info is not None:
        if in_expense:
            return [send_message(chat, info), *expense_flow.prompt(db, session, employee, chat)]
        _reset(session, MENU)
        return [send_message(chat, info), menu_action(chat, employee)]

    if (choice or "").startswith(fix_flow.CB_OPEN) and not in_expense:
        return fix_flow.open_expense(db, session, employee, chat, choice.removeprefix(fix_flow.CB_OPEN))
    if choice == CB_EXPENSE and not in_expense:
        return expense_flow.start(db, session, employee, chat)
    if in_fix:
        return fix_flow.handle(db, session, employee, incoming)
    if incoming.file is not None and not in_expense:
        return expense_flow.start_with_file(db, session, employee, incoming)
    if in_expense:
        return expense_flow.handle(db, session, employee, incoming)

    # Texto libre, botón viejo o estado desconocido: menú principal
    _reset(session, MENU)
    return [menu_action(chat, employee)]


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


# --- Archivos ya descargados por n8n (acción cm:store_file) ---


def handle_file(
    db: Session,
    storage: Storage,
    *,
    telegram_user_id: int,
    purpose: str,
    data: bytes,
    file_name: str | None,
    file_unique_id: str | None,
) -> list[Action]:
    """Guarda el archivo de inmediato (regla fundamental) y luego continúa la conversación."""
    employee = db.scalar(select(Employee).where(Employee.telegram_user_id == telegram_user_id))
    if employee is None:
        return []  # no hay empresa a la cual asociarlo; el bot ya le pidió registrarse
    chat = employee.telegram_chat_id or telegram_user_id
    if employee.status != EmployeeStatus.ACTIVE or employee.deleted_at is not None:
        return [send_message(chat, msg.EMPLOYEE_INACTIVE)]

    session = db.get(BotSession, telegram_user_id)
    if session is None:
        session = BotSession(telegram_user_id=telegram_user_id, employee_id=employee.id, state=MENU, data={})
        db.add(session)

    kind = DocumentKind.RUT if purpose == expense_flow.RUT_FILE else DocumentKind.INVOICE_PHOTO
    try:
        stored = documents.store_document(
            db, storage, company_id=employee.company_id, data=data, kind=kind,
            actor=Actor.from_employee(employee), employee_id=employee.id,
            original_filename=file_name, telegram_file_unique_id=file_unique_id,
        )
    except (UnsupportedFileError, InvalidFileError, FileTooLargeError) as exc:
        reply = [send_message(chat, msg.EXP_FILE_INVALID.format(reason=html.escape(str(exc))))]
        if expense_flow.is_active(session):
            reply += expense_flow.prompt(db, session, employee, chat)
        return reply
    if fix_flow.is_active(session):
        actions = fix_flow.on_file_stored(db, session, employee, chat, stored.document)
    else:
        actions = expense_flow.on_file_stored(db, session, employee, chat, stored.document, purpose)
    db.flush()
    return actions
