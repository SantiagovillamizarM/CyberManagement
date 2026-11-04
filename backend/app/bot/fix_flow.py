"""Consulta y corrección de gastos por el propio empleado (paso 4.4).

A diferencia del registro, aquí cada corrección se guarda AL INSTANTE en el gasto,
a través de services.expenses.update_expense. Así se aplican las mismas reglas que
en el panel: permisos, historial campo por campo, bloqueo optimista y duplicados.

    🧾 Mis gastos ─► detalle ─► ✏️ Corregir ─► campo ─► valor ─► detalle actualizado
                            └─► 📤 Reenviar (rechazados)
"""

import html
import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.bot import expense_flow as exp
from app.bot import inputs
from app.bot import messages as msg
from app.bot.actions import Action, Keyboard, send_message
from app.bot.inputs import InvalidInput
from app.bot.menu import MENU, menu_action
from app.bot.updates import Incoming, Kind
from app.models import BotSession, Category, Document, Employee, Expense, Supplier
from app.models.enums import DocumentKind, ExpenseStatus
from app.services import catalog, documents, expenses
from app.services.audit import Actor
from app.utils.dates import InvalidDateError, local_today, parse_invoice_date
from app.utils.money import format_cop
from app.utils.nit import InvalidNitError, parse_nit

# --- Estados ---
DETAIL = "fix:detail"
FIELD = "fix:field"
DESCRIPTION = "fix:description"
TOTAL = "fix:total"
ISSUE_DATE = "fix:issue_date"
NIT = "fix:nit"
SUPPLIER_NAME = "fix:supplier_name"
INVOICE_NUMBER = "fix:invoice_number"
CATEGORY = "fix:category"
PHOTO = "fix:photo"

FIELDS = {
    "description": DESCRIPTION,
    "total": TOTAL,
    "issue_date": ISSUE_DATE,
    "nit": NIT,
    "invoice_number": INVOICE_NUMBER,
    "category": CATEGORY,
    "photo": PHOTO,
}
FIELD_LABELS = {**{k: v for k, v in msg.EXP_FIELD_LABELS.items() if k in FIELDS}, "photo": msg.FIX_ADD_PHOTO}

# --- Botones ---
CB_OPEN = "fix:open:"  # + id del gasto
CB_EDIT, CB_RESUBMIT, CB_LIST, CB_BACK = "fix:edit", "fix:resubmit", "fix:list", "fix:back"
CB_FIELD = "fix:field:"

RESUBMITTABLE = {ExpenseStatus.DRAFT, ExpenseStatus.PENDING_COMPLETION, ExpenseStatus.REJECTED}
LIST_LIMIT = 10


def is_active(session: BotSession) -> bool:
    return session.state.startswith("fix:")


def _load(db: Session, employee: Employee, expense_id: Any) -> Expense | None:
    """El gasto, solo si es del empleado. Uno ajeno se trata como inexistente."""
    try:
        expense = expenses.get_expense(db, employee.company_id, uuid.UUID(str(expense_id)))
    except (ValueError, expenses.ExpenseNotFoundError):
        return None
    return expense if expense.employee_id == employee.id else None


def _remember(session: BotSession, expense: Expense, state: str = DETAIL, **extra: Any) -> None:
    session.state = state
    session.data = {"flow": "fix", "expense_id": str(expense.id), "version": expense.version, **extra}


def _status_label(expense: Expense) -> str:
    return msg.EXPENSE_STATUS_LABELS.get(expense.status.value, expense.status.value)


# --- Lista y detalle ---


def list_expenses(db: Session, session: BotSession, employee: Employee, chat_id: int) -> list[Action]:
    session.state = MENU
    session.data = {}
    recent = db.scalars(
        select(Expense)
        .where(Expense.employee_id == employee.id, Expense.deleted_at.is_(None))
        .order_by(Expense.received_at.desc())
        .limit(LIST_LIMIT)
    ).all()
    if not recent:
        return [send_message(chat_id, msg.NO_EXPENSES), menu_action(chat_id, employee)]
    rows: Keyboard = []
    for expense in recent:
        when = (expense.issue_date or expense.received_at.date()).strftime("%d/%m")
        lock = " 🔒" if not expenses.employee_can_edit(db, expense) else ""
        label = f"{when} · {(expense.description or 'Sin descripción')[:18]} · {format_cop(expense.total)} · {_status_label(expense)}{lock}"
        rows.append([(label, f"{CB_OPEN}{expense.id}")])
    return [send_message(chat_id, msg.FIX_LIST_HEADER, rows)]


def open_expense(db: Session, session: BotSession, employee: Employee, chat_id: int, expense_id: str) -> list[Action]:
    expense = _load(db, employee, expense_id)
    if expense is None:
        return [send_message(chat_id, msg.FIX_NOT_FOUND), *list_expenses(db, session, employee, chat_id)]
    _remember(session, expense)
    return [_detail(db, expense, chat_id)]


def _detail(db: Session, expense: Expense, chat_id: int) -> Action:
    supplier = db.get(Supplier, expense.supplier_id) if expense.supplier_id else None
    category = db.get(Category, expense.category_id) if expense.category_id else None
    photos = db.scalar(
        select(func.count()).where(Document.expense_id == expense.id, Document.kind == DocumentKind.INVOICE_PHOTO,
                                   Document.deleted_at.is_(None))
    )
    when = expense.issue_date or expense.received_at.date()
    text = msg.FIX_DETAIL.format(
        date=when.strftime("%d/%m/%Y"),
        description=html.escape(expense.description or "—"),
        total=format_cop(expense.total),
        supplier=f"{html.escape(supplier.name)} (NIT {supplier.nit}-{supplier.dv})" if supplier else msg.NO_NIT,
        invoice_number=html.escape(expense.invoice_number or "—"),
        category=html.escape(category.name if category else "—"),
        photos=photos or "Sin foto",
        status=_status_label(expense),
    )
    editable = expenses.employee_can_edit(db, expense)
    if expense.status == ExpenseStatus.REJECTED and expense.rejection_reason:
        text += msg.FIX_REJECTED.format(reason=html.escape(expense.rejection_reason))
    buttons: Keyboard = []
    if editable:
        row = [(msg.FIX_EDIT, CB_EDIT)]
        if expense.status in RESUBMITTABLE:
            row.append((msg.FIX_RESUBMIT, CB_RESUBMIT))
            text += msg.FIX_CAN_RESUBMIT
        buttons.append(row)
    else:
        text += msg.FIX_LOCKED
    buttons.append([(msg.FIX_BACK_TO_LIST, CB_LIST)])
    return send_message(chat_id, text, buttons)


# --- Preguntas ---


def prompt(db: Session, session: BotSession, employee: Employee, chat_id: int, expense: Expense) -> list[Action]:
    state = session.state
    skip: Keyboard = [[(msg.SKIP, exp.CB_SKIP)]]
    if state == DETAIL:
        return [_detail(db, expense, chat_id)]
    if state == FIELD:
        fields = [(label, f"{CB_FIELD}{key}") for key, label in FIELD_LABELS.items()]
        rows = [fields[i : i + 2] for i in range(0, len(fields), 2)]
        rows.append([(msg.EXP_BACK_TO_SUMMARY, CB_BACK)])
        return [send_message(chat_id, msg.FIX_WHICH_FIELD, rows)]
    if state == DESCRIPTION:
        return [send_message(chat_id, msg.EXP_ASK_DESCRIPTION)]
    if state == TOTAL:
        return [send_message(chat_id, msg.EXP_ASK_TOTAL)]
    if state == ISSUE_DATE:
        return [send_message(chat_id, msg.EXP_ASK_DATE,
                             [[(msg.TODAY, exp.CB_TODAY), (msg.YESTERDAY, exp.CB_YESTERDAY)]])]
    if state == NIT:
        return [send_message(chat_id, msg.EXP_ASK_NIT, skip)]
    if state == SUPPLIER_NAME:
        return [send_message(chat_id, msg.EXP_ASK_SUPPLIER_NAME)]
    if state == INVOICE_NUMBER:
        return [send_message(chat_id, msg.EXP_ASK_INVOICE, skip)]
    if state == CATEGORY:
        categories = catalog.list_categories(db, employee.company_id)
        buttons = [(c.name, f"{exp.CB_CATEGORY}{c.id}") for c in categories]
        return [send_message(chat_id, msg.EXP_ASK_CATEGORY, [buttons[i : i + 2] for i in range(0, len(buttons), 2)])]
    if state == PHOTO:
        return [send_message(chat_id, msg.FIX_ASK_PHOTO, [[(msg.EXP_BACK_TO_SUMMARY, CB_BACK)]])]
    return []


# --- Respuestas ---


def handle(db: Session, session: BotSession, employee: Employee, incoming: Incoming) -> list[Action]:
    chat = incoming.chat_id
    expense = _load(db, employee, (session.data or {}).get("expense_id"))
    if expense is None:
        session.state, session.data = MENU, {}
        return [send_message(chat, msg.FIX_NOT_FOUND), menu_action(chat, employee)]

    choice = incoming.callback_data
    if choice == CB_LIST:
        return list_expenses(db, session, employee, chat)
    if choice == CB_BACK:
        _remember(session, expense)
        return [_detail(db, expense, chat)]
    if incoming.file is not None:
        if session.state != PHOTO:
            return [send_message(chat, msg.FIX_PHOTO_VIA_MENU), *prompt(db, session, employee, chat, expense)]
        try:
            return inputs.request_file(incoming, inputs.INVOICE)
        except InvalidInput as error:
            return [send_message(chat, error.message), *prompt(db, session, employee, chat, expense)]

    actor = Actor.from_employee(employee)
    try:
        if session.state == DETAIL:
            if choice == CB_EDIT:
                _assert_editable(db, expense)
                session.state = FIELD
                return prompt(db, session, employee, chat, expense)
            if choice == CB_RESUBMIT:
                with db.begin_nested():
                    expenses.submit(db, expense, actor)
                _remember(session, expense)
                return [send_message(chat, msg.FIX_RESUBMITTED), _detail(db, expense, chat)]
            raise InvalidInput()
        if session.state == FIELD:
            field = (choice or "").removeprefix(CB_FIELD)
            if not (choice or "").startswith(CB_FIELD) or field not in FIELDS:
                raise InvalidInput()
            session.state = FIELDS[field]
            return prompt(db, session, employee, chat, expense)
        return _apply(db, session, employee, incoming, expense, actor)
    except InvalidInput as error:
        return [send_message(chat, error.message), *prompt(db, session, employee, chat, expense)]
    except expenses.ExpenseLockedError as exc:
        _remember(session, expense)
        return [send_message(chat, msg.FIX_LOCKED_ERROR.format(reason=html.escape(str(exc)))),
                _detail(db, expense, chat)]
    except expenses.VersionConflictError:
        db.refresh(expense)
        _remember(session, expense)
        return [send_message(chat, msg.FIX_CONFLICT), _detail(db, expense, chat)]
    except expenses.DuplicateInvoiceError as exc:
        return [send_message(chat, msg.EXP_DUPLICATE.format(reason=exc.reason.lower())),
                *prompt(db, session, employee, chat, expense)]
    except expenses.ExpenseRuleError as exc:
        _remember(session, expense)
        return [send_message(chat, msg.EXP_COULD_NOT_SAVE.format(reason=html.escape(str(exc)))),
                _detail(db, expense, chat)]


def _assert_editable(db: Session, expense: Expense) -> None:
    if not expenses.employee_can_edit(db, expense):
        raise expenses.ExpenseLockedError("Este gasto ya no se puede modificar")


def _apply(db: Session, session: BotSession, employee: Employee, incoming: Incoming,
           expense: Expense, actor: Actor) -> list[Action]:
    """Lee el valor del campo en curso y lo guarda en el gasto."""
    state = session.state
    chat = incoming.chat_id
    changes: dict[str, Any] = {}
    supplier: dict[str, Any] = {}

    if state == DESCRIPTION:
        changes["description"] = inputs.text(incoming, min_len=3, max_len=500)
    elif state == TOTAL:
        changes["total"] = inputs.amount(incoming)
    elif state == ISSUE_DATE:
        changes["issue_date"] = _date(incoming)
    elif state == NIT:
        if inputs.skipped(incoming):
            supplier = {"supplier_nit": None}
        else:
            nit = _nit(incoming)
            known = db.scalar(select(Supplier).where(Supplier.company_id == employee.company_id,
                                                     Supplier.nit == nit.number))
            if known is None or known.name.startswith("NIT "):
                _remember(session, expense, SUPPLIER_NAME, pending_nit=str(nit))
                return prompt(db, session, employee, chat, expense)
            supplier = {"supplier_nit": str(nit), "supplier_name": known.name}
    elif state == SUPPLIER_NAME:
        name = inputs.text(incoming, min_len=2, max_len=200)
        supplier = {"supplier_nit": session.data["pending_nit"], "supplier_name": name}
    elif state == INVOICE_NUMBER:
        changes["invoice_number"] = None if inputs.skipped(incoming) else inputs.text(incoming, min_len=1, max_len=50)
    elif state == CATEGORY:
        changes["category_id"] = _category(db, employee, incoming)
    else:
        raise InvalidInput()

    before = expense.version
    # Punto de guardado: si la corrección falla (duplicado, conflicto...), el gasto queda como estaba.
    with db.begin_nested():
        expenses.update_expense(db, expense, changes, version=session.data["version"], actor=actor, **supplier)
    _remember(session, expense)
    note = msg.FIX_SAVED if expense.version != before else msg.FIX_NO_CHANGES
    return [send_message(chat, note), _detail(db, expense, chat)]


def _date(incoming: Incoming):
    if incoming.callback_data == exp.CB_TODAY:
        return local_today()
    if incoming.callback_data == exp.CB_YESTERDAY:
        return parse_invoice_date("ayer")
    if incoming.kind != Kind.TEXT:
        raise InvalidInput()
    try:
        return parse_invoice_date(incoming.text or "")
    except InvalidDateError as exc:
        raise InvalidInput(f"{exc}.")


def _nit(incoming: Incoming):
    if incoming.kind != Kind.TEXT:
        raise InvalidInput()
    try:
        return parse_nit(incoming.text or "")
    except InvalidNitError as exc:
        raise InvalidInput(f"❌ {exc}. Revísalo en la factura e inténtalo de nuevo.")


def _category(db: Session, employee: Employee, incoming: Incoming) -> uuid.UUID:
    choice = incoming.callback_data or ""
    if not choice.startswith(exp.CB_CATEGORY):
        raise InvalidInput()
    try:
        return catalog.get_category(db, employee.company_id, uuid.UUID(choice.removeprefix(exp.CB_CATEGORY))).id
    except (ValueError, catalog.CategoryNotFoundError):
        raise InvalidInput()


# --- Foto agregada (después de que n8n la descargó) ---


def on_file_stored(db: Session, session: BotSession, employee: Employee, chat_id: int,
                   document: Document) -> list[Action]:
    expense = _load(db, employee, (session.data or {}).get("expense_id"))
    if expense is None or session.state != PHOTO:
        return [send_message(chat_id, msg.EXP_FILE_NO_FLOW), menu_action(chat_id, employee)]
    if document.expense_id not in (None, expense.id):
        return [send_message(chat_id, msg.EXP_PHOTO_IN_OTHER_EXPENSE), *prompt(db, session, employee, chat_id, expense)]
    if not expenses.employee_can_edit(db, expense):
        _remember(session, expense)
        return [send_message(chat_id, msg.FIX_LOCKED_ERROR.format(reason="Este gasto ya no se puede modificar")),
                _detail(db, expense, chat_id)]
    documents.attach_to_expense(db, document, expense, Actor.from_employee(employee))
    _remember(session, expense)
    return [send_message(chat_id, msg.FIX_PHOTO_ADDED), _detail(db, expense, chat_id)]
