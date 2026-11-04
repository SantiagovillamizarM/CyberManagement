"""Conversación guiada para registrar un gasto.

Cada paso es un estado "exp:<paso>" en bot_sessions. Lo que el empleado va
respondiendo se guarda en bot_sessions.data, así que la conversación sobrevive a
reinicios de la API. Los gastos y viáticos NO se escriben hasta que confirma.

EXCEPCIÓN (regla fundamental): las fotos y archivos SÍ se guardan apenas llegan
(tabla documents), aunque luego cancele. Nunca se pierde una foto de una factura.

Con la foto, la IA lee la factura (Fase 5, escenarios A/B/C del requerimiento 3.1).
En el escenario A los datos leídos se llenan solos y esos pasos se SALTAN
(data["ai_skip"]); solo se pregunta lo que falte y al final se muestra el resumen.

    [viático] ─► foto ─► descripción ─► total ─► valor unitario ─► fecha ─► NIT
              ─► (nombre ─► RUT) ─► n.º factura ─► categoría ─► ubicación ─► resumen
"""

import html
import uuid
from datetime import date, time
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import messages as msg
from app.bot import inputs
from app.bot.actions import Action, Keyboard, send_message
from app.bot.inputs import ALLOWED_MIME, INVOICE, RUT_FILE, InvalidInput  # noqa: F401 (reexportados)
from app.bot.menu import MENU, menu_action
from app.bot.updates import Incoming, Kind
from app.models import BotSession, Document, Employee, Supplier, Viatico
from app.ai.gemini import VisionModel
from app.ai.invoice import InvoiceData
from app.models.enums import DataSource, ExtractionOutcome, LocationSource, MovementSource, PaymentSource, ViaticoStatus
from app.services import audit, catalog, documents, expenses, extraction, viaticos
from app.services.audit import Actor
from app.storage import Storage
from app.utils.dates import InvalidDateError, local_today, parse_invoice_date
from app.utils.money import format_cop
from app.utils.nit import InvalidNitError, parse_nit

# --- Estados ---
WHICH_MONEY = "exp:which_money"  # tiene viáticos abiertos: elige cuál
HAD_MONEY = "exp:had_money"  # no tiene: "¿Contabas con dinero previo...?"
INITIAL_AMOUNT = "exp:initial_amount"
PURPOSE = "exp:purpose"
PHOTO = "exp:photo"
DESCRIPTION = "exp:description"
TOTAL = "exp:total"
UNIT_PRICE = "exp:unit_price"
ISSUE_DATE = "exp:issue_date"
NIT = "exp:nit"
SUPPLIER_NAME = "exp:supplier_name"
RUT = "exp:rut"  # solo para proveedores nuevos
INVOICE_NUMBER = "exp:invoice_number"
CATEGORY = "exp:category"
LOCATION = "exp:location"
CONFIRM = "exp:confirm"
EDIT_CHOICE = "exp:edit_choice"

# Orden de los datos de la factura. Al editar, después de un campo se vuelve al resumen.
FIELD_ORDER = [PHOTO, DESCRIPTION, TOTAL, UNIT_PRICE, ISSUE_DATE, NIT, INVOICE_NUMBER, CATEGORY, LOCATION]
EDITABLE = {
    "photo": PHOTO,
    "description": DESCRIPTION,
    "total": TOTAL,
    "unit_price": UNIT_PRICE,
    "issue_date": ISSUE_DATE,
    "nit": NIT,
    "invoice_number": INVOICE_NUMBER,
    "category": CATEGORY,
    "location": LOCATION,
}

# --- Datos de botones (máx. 64 bytes) ---
CB_SKIP, CB_YES, CB_NO = inputs.CB_SKIP, "exp:yes", "exp:no"
CB_VIATICO, CB_NEW_VIATICO, CB_OWN_MONEY = "exp:via:", "exp:via:new", "exp:via:own"
CB_TODAY, CB_YESTERDAY = "exp:date:today", "exp:date:yesterday"
CB_CATEGORY = "exp:cat:"
CB_CONFIRM, CB_EDIT, CB_CANCEL = "exp:ok", "exp:edit", "exp:cancel"
CB_FIELD, CB_BACK = "exp:field:", "exp:back"
CB_NO_PHOTO = "exp:nophoto"


def is_active(session: BotSession) -> bool:
    return session.state.startswith("exp:")


def _data(session: BotSession) -> dict[str, Any]:
    return dict(session.data or {})


def _set(session: BotSession, **values: Any) -> None:
    # Se reasigna el diccionario completo: SQLAlchemy no detecta cambios internos en JSONB.
    session.data = {**_data(session), **values}


def _open_viaticos(db: Session, employee: Employee) -> list[Viatico]:
    return viaticos.list_viaticos(db, employee.company_id, employee_id=employee.id, status=ViaticoStatus.OPEN)


def _after_money(session: BotSession) -> str:
    """Tras elegir el dinero se pide la foto, salvo que ya la tengamos (la envió desde el menú)."""
    return _next_state(session, PHOTO) if _data(session).get("document_id") else PHOTO


# Pasos con datos de la factura: si el empleado los responde o corrige, el gasto ya no es "solo IA".
INVOICE_DATA_STATES = {DESCRIPTION, TOTAL, UNIT_PRICE, ISSUE_DATE, NIT, SUPPLIER_NAME, INVOICE_NUMBER}


def _next_state(session: BotSession, current: str) -> str:
    """El paso que sigue a `current`, saltando los que ya llenó la IA."""
    data = _data(session)
    skip = set(data.get("ai_skip") or [])
    for state in FIELD_ORDER[FIELD_ORDER.index(current) + 1:]:
        if state not in skip:
            return state
        if state == NIT and data.get("supplier_nit"):
            if not data.get("supplier_name"):
                return SUPPLIER_NAME  # la IA leyó el NIT pero no el nombre
            if data.get("ai_new_supplier"):
                return RUT  # proveedor nuevo: se ofrece adjuntar su RUT (opcional)
    return CONFIRM


# --- Inicio ---


def start(db: Session, session: BotSession, employee: Employee, chat_id: int) -> list[Action]:
    session.data = {"flow": "expense"}
    session.draft_expense_id = None
    session.state = WHICH_MONEY if _open_viaticos(db, employee) else HAD_MONEY
    return prompt(db, session, employee, chat_id)


# --- Preguntas de cada paso ---


def prompt(db: Session, session: BotSession, employee: Employee, chat_id: int) -> list[Action]:
    state = session.state
    skip: Keyboard = [[(msg.SKIP, CB_SKIP)]]

    if state == WHICH_MONEY:
        rows: Keyboard = []
        for viatico in _open_viaticos(db, employee):
            remaining = viaticos.compute_balance(db, viatico).remaining
            label = msg.EXP_VIATICO_OPTION.format(
                purpose=(viatico.purpose or "Viático")[:30], remaining=format_cop(remaining)
            )
            rows.append([(label, f"{CB_VIATICO}{viatico.id}")])
        rows.append([(msg.EXP_NEW_VIATICO_OPTION, CB_NEW_VIATICO)])
        rows.append([(msg.EXP_OWN_MONEY_OPTION, CB_OWN_MONEY)])
        return [send_message(chat_id, msg.EXP_WHICH_MONEY, rows)]
    if state == HAD_MONEY:
        return [send_message(chat_id, msg.EXP_HAD_MONEY, [[(msg.YES, CB_YES), (msg.NO, CB_NO)]])]
    if state == INITIAL_AMOUNT:
        return [send_message(chat_id, msg.EXP_ASK_INITIAL)]
    if state == PURPOSE:
        return [send_message(chat_id, msg.EXP_ASK_PURPOSE, skip)]
    if state == PHOTO:
        return [send_message(chat_id, msg.EXP_ASK_PHOTO, [[(msg.EXP_NO_PHOTO, CB_NO_PHOTO)]])]
    if state == RUT:
        return [send_message(chat_id, msg.EXP_ASK_RUT, skip)]
    if state == DESCRIPTION:
        return [send_message(chat_id, msg.EXP_ASK_DESCRIPTION)]
    if state == TOTAL:
        return [send_message(chat_id, msg.EXP_ASK_TOTAL)]
    if state == UNIT_PRICE:
        return [send_message(chat_id, msg.EXP_ASK_UNIT, skip)]
    if state == ISSUE_DATE:
        return [send_message(chat_id, msg.EXP_ASK_DATE, [[(msg.TODAY, CB_TODAY), (msg.YESTERDAY, CB_YESTERDAY)]])]
    if state == NIT:
        return [send_message(chat_id, msg.EXP_ASK_NIT, skip)]
    if state == SUPPLIER_NAME:
        return [send_message(chat_id, msg.EXP_ASK_SUPPLIER_NAME)]
    if state == INVOICE_NUMBER:
        return [send_message(chat_id, msg.EXP_ASK_INVOICE, skip)]
    if state == CATEGORY:
        categories = catalog.list_categories(db, employee.company_id)
        buttons = [(c.name, f"{CB_CATEGORY}{c.id}") for c in categories]
        rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
        return [send_message(chat_id, msg.EXP_ASK_CATEGORY, rows)]
    if state == LOCATION:
        keyboard = [[{"text": msg.EXP_SHARE_LOCATION, "request_location": True}], [{"text": msg.SKIP}]]
        return [send_message(chat_id, msg.EXP_ASK_LOCATION, reply_keyboard=keyboard)]
    if state == CONFIRM:
        buttons = [[(msg.EXP_CONFIRM, CB_CONFIRM), (msg.EXP_EDIT, CB_EDIT)], [(msg.EXP_CANCEL, CB_CANCEL)]]
        return [send_message(chat_id, _summary(db, session), buttons)]
    if state == EDIT_CHOICE:
        fields = [(label, f"{CB_FIELD}{key}") for key, label in msg.EXP_FIELD_LABELS.items()]
        rows = [fields[i : i + 2] for i in range(0, len(fields), 2)]
        rows.append([(msg.EXP_BACK_TO_SUMMARY, CB_BACK)])
        return [send_message(chat_id, msg.EXP_WHICH_FIELD, rows)]
    return []


# --- Respuestas ---


def handle(db: Session, session: BotSession, employee: Employee, incoming: Incoming) -> list[Action]:
    """Procesa la respuesta del paso actual.

    Cada manejador devuelve:
    - una TUPLA: mensajes previos (puede estar vacía); luego se agrega la pregunta del
      siguiente paso automáticamente;
    - una LISTA: la respuesta completa (ej. al guardar o cancelar); no se agrega nada.
    """
    chat = incoming.chat_id
    if incoming.file is not None:  # las fotos y archivos se atienden en cualquier paso
        return _on_file(db, session, employee, incoming)
    answered = session.state
    try:
        result = _HANDLERS[answered](db, session, employee, incoming)
    except InvalidInput as error:
        return [send_message(chat, error.message), *prompt(db, session, employee, chat)]
    if answered in INVOICE_DATA_STATES and is_active(session) and _data(session).get("ai"):
        _set(session, ai_edited=True)  # el empleado escribió o corrigió un dato de la factura
    if isinstance(result, list):
        return result
    return [*result, *prompt(db, session, employee, chat)]





def _advance(session: BotSession, current: str) -> tuple[Any, ...]:
    """Pasa al siguiente paso. Si se estaba editando un campo, vuelve al resumen."""
    data = _data(session)
    if data.get("editing"):
        session.state = CONFIRM
        _set(session, editing=False)
    elif current in FIELD_ORDER:
        session.state = _next_state(session, current)
    else:
        session.state = CONFIRM
    return ()


def _on_which_money(db, session, employee, incoming):
    choice = incoming.callback_data or ""
    if choice == CB_OWN_MONEY:
        _set(session, payment_source=PaymentSource.OWN_MONEY.value, viatico_id=None, new_viatico=None)
    elif choice == CB_NEW_VIATICO:
        session.state = INITIAL_AMOUNT
        return ()
    elif choice.startswith(CB_VIATICO):
        try:
            viatico_id = uuid.UUID(choice.removeprefix(CB_VIATICO))
        except ValueError:
            raise InvalidInput()
        if viatico_id not in {v.id for v in _open_viaticos(db, employee)}:
            raise InvalidInput()  # botón viejo de un viático que ya se cerró
        _set(session, payment_source=PaymentSource.VIATICO.value, viatico_id=str(viatico_id), new_viatico=None)
    else:
        raise InvalidInput()
    session.state = _after_money(session)
    return ()


def _on_had_money(db, session, employee, incoming):
    if incoming.callback_data == CB_YES:
        session.state = INITIAL_AMOUNT
    elif incoming.callback_data == CB_NO:
        _set(session, payment_source=PaymentSource.OWN_MONEY.value, viatico_id=None, new_viatico=None)
        session.state = _after_money(session)
    else:
        raise InvalidInput()
    return ()


def _on_initial_amount(db, session, employee, incoming):
    amount = inputs.amount(incoming)
    _set(session, payment_source=PaymentSource.VIATICO.value, viatico_id=None,
         new_viatico={"amount": str(amount), "purpose": None})
    session.state = PURPOSE
    return ()


def _on_purpose(db, session, employee, incoming):
    purpose = None if inputs.skipped(incoming) else inputs.text(incoming, min_len=3, max_len=200)
    _set(session, new_viatico={**_data(session)["new_viatico"], "purpose": purpose})
    session.state = _after_money(session)
    return ()


def _on_description(db, session, employee, incoming):
    _set(session, description=inputs.text(incoming, min_len=3, max_len=500))
    return _advance(session, DESCRIPTION)


def _on_total(db, session, employee, incoming):
    total = inputs.amount(incoming)
    unit = _data(session).get("unit_price")
    if unit and Decimal(unit) > total:
        _set(session, unit_price=None)  # el unitario anterior ya no es coherente
    if total != Decimal(_data(session).get("total") or "-1"):
        # Los productos leídos por la IA ya no cuadran con el total corregido.
        _set(session, items=None)
    _set(session, total=str(total))
    return _advance(session, TOTAL)


def _on_unit_price(db, session, employee, incoming):
    if inputs.skipped(incoming):
        _set(session, unit_price=None)
    else:
        unit = inputs.amount(incoming)
        if unit > Decimal(_data(session)["total"]):
            raise InvalidInput(msg.EXP_UNIT_GT_TOTAL)
        _set(session, unit_price=str(unit))
    return _advance(session, UNIT_PRICE)


def _on_issue_date(db, session, employee, incoming):
    if incoming.callback_data == CB_TODAY:
        value = local_today()
    elif incoming.callback_data == CB_YESTERDAY:
        value = parse_invoice_date("ayer")
    elif incoming.kind == Kind.TEXT:
        try:
            value = parse_invoice_date(incoming.text or "")
        except InvalidDateError as exc:
            raise InvalidInput(f"{exc}.")
    else:
        raise InvalidInput()
    _set(session, issue_date=value.isoformat())
    return _advance(session, ISSUE_DATE)


def _on_nit(db, session, employee, incoming):
    if inputs.skipped(incoming):
        _set(session, supplier_nit=None, supplier_name=None)
        return _advance(session, NIT)
    if incoming.kind != Kind.TEXT:
        raise InvalidInput()
    try:
        nit = parse_nit(incoming.text or "")
    except InvalidNitError as exc:
        raise InvalidInput(f"❌ {exc}. Revísalo en la factura e inténtalo de nuevo.")
    known = db.scalar(
        select(Supplier).where(Supplier.company_id == employee.company_id, Supplier.nit == nit.number)
    )
    _set(session, supplier_nit=str(nit))
    if known is None or known.name.startswith("NIT "):
        _set(session, supplier_name=None)
        session.state = SUPPLIER_NAME  # también al editar: hay que saber el nombre
        return ()
    _set(session, supplier_name=known.name)
    _advance(session, NIT)
    return (send_message(incoming.chat_id, msg.EXP_KNOWN_SUPPLIER.format(name=html.escape(known.name))),)


def _on_supplier_name(db, session, employee, incoming):
    _set(session, supplier_name=inputs.text(incoming, min_len=2, max_len=200))
    session.state = RUT  # proveedor nuevo: se ofrece adjuntar su RUT
    return ()


def _on_rut(db, session, employee, incoming):
    if not inputs.skipped(incoming):
        raise InvalidInput()
    return _advance(session, NIT)


def _on_photo(db, session, employee, incoming):
    if incoming.callback_data != CB_NO_PHOTO:
        raise InvalidInput(msg.EXP_ASK_PHOTO)
    _set(session, document_id=None, document_gps=False)  # al editar: quita la foto
    if _data(session).get("ai"):
        _set(session, ai_edited=True)
    return _advance(session, PHOTO)


def _on_invoice_number(db, session, employee, incoming):
    value = None if inputs.skipped(incoming) else inputs.text(incoming, min_len=1, max_len=50)
    _set(session, invoice_number=value)
    return _advance(session, INVOICE_NUMBER)


def _on_category(db, session, employee, incoming):
    choice = incoming.callback_data or ""
    if not choice.startswith(CB_CATEGORY):
        raise InvalidInput()
    try:
        category = catalog.get_category(db, employee.company_id, uuid.UUID(choice.removeprefix(CB_CATEGORY)))
    except (ValueError, catalog.CategoryNotFoundError):
        raise InvalidInput()
    _set(session, category_id=str(category.id), category_name=category.name)
    return _advance(session, CATEGORY)


def _on_location(db, session, employee, incoming):
    chat = incoming.chat_id
    if incoming.kind == Kind.LOCATION and incoming.latitude is not None and incoming.longitude is not None:
        _set(session, location={"lat": str(incoming.latitude), "lon": str(incoming.longitude)})
        note = msg.EXP_LOCATION_SAVED
    elif inputs.skipped(incoming):
        _set(session, location=None)
        note = msg.EXP_LOCATION_SKIPPED
    else:
        raise InvalidInput()
    _advance(session, LOCATION)
    # El teclado de ubicación se quita con un mensaje propio (un mensaje solo admite un teclado).
    return (send_message(chat, note, remove_keyboard=True),)


def _on_confirm(db, session, employee, incoming):
    if incoming.callback_data == CB_CONFIRM:
        return _save(db, session, employee, incoming.chat_id)
    if incoming.callback_data == CB_EDIT:
        session.state = EDIT_CHOICE
        return ()
    if incoming.callback_data == CB_CANCEL:
        return cancel(session, employee, incoming.chat_id)
    raise InvalidInput()


def _on_edit_choice(db, session, employee, incoming):
    choice = incoming.callback_data or ""
    if choice == CB_BACK:
        session.state = CONFIRM
        return ()
    field = choice.removeprefix(CB_FIELD)
    if not choice.startswith(CB_FIELD) or field not in EDITABLE:
        raise InvalidInput()
    _set(session, editing=True)
    session.state = EDITABLE[field]
    return ()


_HANDLERS = {
    WHICH_MONEY: _on_which_money,
    HAD_MONEY: _on_had_money,
    INITIAL_AMOUNT: _on_initial_amount,
    PURPOSE: _on_purpose,
    DESCRIPTION: _on_description,
    TOTAL: _on_total,
    UNIT_PRICE: _on_unit_price,
    ISSUE_DATE: _on_issue_date,
    NIT: _on_nit,
    SUPPLIER_NAME: _on_supplier_name,
    RUT: _on_rut,
    PHOTO: _on_photo,
    INVOICE_NUMBER: _on_invoice_number,
    CATEGORY: _on_category,
    LOCATION: _on_location,
    CONFIRM: _on_confirm,
    EDIT_CHOICE: _on_edit_choice,
}


# --- Resumen, guardado y cancelación ---


def _summary(db: Session, session: BotSession) -> str:
    data = _data(session)
    if data.get("supplier_nit"):
        supplier = f"{html.escape(data.get('supplier_name') or '')} (NIT {data['supplier_nit']})"
    else:
        supplier = msg.NO_NIT
    if data.get("new_viatico"):
        new = data["new_viatico"]
        payment = msg.EXP_PAY_NEW_VIATICO.format(
            purpose=html.escape(new.get("purpose") or "Viático"), amount=format_cop(Decimal(new["amount"]))
        )
    elif data.get("viatico_id"):
        viatico = db.get(Viatico, uuid.UUID(data["viatico_id"]))
        payment = msg.EXP_PAY_VIATICO.format(purpose=html.escape((viatico.purpose if viatico else None) or "Viático"))
    else:
        payment = msg.EXP_PAY_OWN

    def money(key: str) -> str:
        return format_cop(Decimal(data[key])) if data.get(key) else "—"

    ai = bool(data.get("ai"))
    return msg.EXP_SUMMARY.format(
        title=msg.EXP_SUMMARY_TITLE_AI if ai else msg.EXP_SUMMARY_TITLE,
        question=msg.EXP_SUMMARY_QUESTION_AI if ai else msg.EXP_SUMMARY_QUESTION,
        description=html.escape(data.get("description") or "—"),
        total=money("total"),
        amounts=_amounts_block(data, money),
        issue_date=date.fromisoformat(data["issue_date"]).strftime("%d/%m/%Y") if data.get("issue_date") else "—",
        supplier=supplier,
        invoice_number=html.escape(data.get("invoice_number") or "—"),
        category=html.escape(data.get("category_name") or "—"),
        payment=payment,
        location=_location_label(data),
        photo=("Adjunta ✅ (con GPS)" if data.get("document_gps") else "Adjunta ✅")
        if data.get("document_id") else "Sin foto",
    )


MAX_SUMMARY_ITEMS = 6


def _amounts_block(data: dict[str, Any], money) -> str:
    """Valor unitario (manual) o, si la IA los leyó, los productos y los impuestos."""
    if data.get("unit_price") or not data.get("items"):
        block = msg.EXP_SUMMARY_UNIT.format(unit_price=money("unit_price"))
    else:
        items = data["items"]
        lines = [
            msg.EXP_SUMMARY_ITEM.format(
                quantity=format(Decimal(i["quantity"]).normalize(), "f"),
                description=html.escape(i["description"][:40]),
                line_total=format_cop(Decimal(i["line_total"])) if i.get("line_total") else "—",
            )
            for i in items[:MAX_SUMMARY_ITEMS]
        ]
        if len(items) > MAX_SUMMARY_ITEMS:
            lines.append(msg.EXP_SUMMARY_MORE_ITEMS.format(count=len(items) - MAX_SUMMARY_ITEMS))
        block = msg.EXP_SUMMARY_ITEMS.format(lines="\n".join(lines))
    if data.get("subtotal") or data.get("tax_total"):
        block += msg.EXP_SUMMARY_TAXES.format(subtotal=money("subtotal"), tax_total=money("tax_total"))
    return block


def _location_label(data: dict[str, Any]) -> str:
    if data.get("location"):
        return "Compartida ✅"
    if data.get("document_gps"):
        return "Del GPS de la foto ✅"
    return msg.NOT_AVAILABLE


def _save(db: Session, session: BotSession, employee: Employee, chat_id: int) -> list[Action]:
    data = _data(session)
    actor = Actor.from_employee(employee)
    try:
        # Punto de guardado: si algo falla, no queda ni el viático nuevo ni el gasto.
        with db.begin_nested():
            viatico_id = uuid.UUID(data["viatico_id"]) if data.get("viatico_id") else None
            if data.get("new_viatico"):
                new = data["new_viatico"]
                viatico_id = viaticos.open_viatico(
                    db, employee, initial_amount=Decimal(new["amount"]), purpose=new.get("purpose"),
                    source=MovementSource.EMPLOYEE, actor=actor,
                ).id
            fields: dict[str, Any] = {
                "description": data["description"],
                "total": Decimal(data["total"]),
                "issue_date": date.fromisoformat(data["issue_date"]),
                "invoice_number": data.get("invoice_number"),
                "category_id": uuid.UUID(data["category_id"]) if data.get("category_id") else None,
                "viatico_id": viatico_id,
                "payment_source": PaymentSource(data["payment_source"]),
                # Datos que solo llegan leídos por la IA:
                "issue_time": time.fromisoformat(data["issue_time"]) if data.get("issue_time") else None,
                "subtotal": Decimal(data["subtotal"]) if data.get("subtotal") else None,
                "tax_total": Decimal(data["tax_total"]) if data.get("tax_total") else None,
                "cufe": data.get("cufe"),
            }
            if data.get("location"):
                fields["location_lat"] = Decimal(data["location"]["lat"])
                fields["location_lon"] = Decimal(data["location"]["lon"])
                fields["location_text"] = "Compartida por el empleado en Telegram al registrar el gasto"
            items = []
            if data.get("unit_price"):
                unit = Decimal(data["unit_price"])
                quantity = (fields["total"] / unit).quantize(Decimal("0.001"))
                items = [{"description": data["description"][:255], "quantity": quantity,
                          "unit_price": unit, "line_total": fields["total"]}]
            elif data.get("items"):  # productos leídos por la IA
                items = [{"description": i["description"], "quantity": Decimal(i["quantity"]),
                          "unit_price": Decimal(i["unit_price"]) if i.get("unit_price") else None,
                          "line_total": Decimal(i["line_total"]) if i.get("line_total") else None}
                         for i in data["items"]]
            expense = expenses.create_expense(
                db, employee, fields, items=items,
                supplier_nit=data.get("supplier_nit"), supplier_name=data.get("supplier_name"),
                actor=actor, location_source=LocationSource.TELEGRAM if data.get("location") else None,
                data_source=_data_source(data),
            )
            if data.get("document_id"):
                document = db.get(Document, uuid.UUID(data["document_id"]))
                if document is not None and document.expense_id in (None, expense.id):
                    # Si la foto trae GPS y no se compartió ubicación, el gasto queda con origen EXIF.
                    documents.attach_to_expense(db, document, expense, actor)
            if data.get("rut_document_id") and expense.supplier_id:
                rut = db.get(Document, uuid.UUID(data["rut_document_id"]))
                if rut is not None and rut.supplier_id is None:
                    rut.supplier_id = expense.supplier_id
                    audit.log(db, company_id=expense.company_id, entity_type="document", entity_id=rut.id,
                              action="attach_rut", actor=actor, field="supplier_id", new_value=expense.supplier_id)
            expenses.submit(db, expense, actor)
    except expenses.DuplicateInvoiceError as exc:
        session.state = CONFIRM
        return [send_message(chat_id, msg.EXP_DUPLICATE.format(reason=exc.reason.lower())),
                *prompt(db, session, employee, chat_id)]
    except (expenses.ExpenseRuleError, viaticos.ViaticoRuleError) as exc:
        session.state = CONFIRM
        return [send_message(chat_id, msg.EXP_COULD_NOT_SAVE.format(reason=html.escape(str(exc)))),
                *prompt(db, session, employee, chat_id)]

    text = msg.EXP_SAVED
    if expense.viatico_id:
        viatico = db.get(Viatico, expense.viatico_id)
        text += msg.EXP_SAVED_BALANCE.format(
            purpose=html.escape(viatico.purpose or "Viático"),
            remaining=format_cop(viaticos.compute_balance(db, viatico).remaining),
        )
    elif expense.is_reimbursable:
        text += msg.EXP_SAVED_REIMBURSABLE
    session.state = MENU
    session.data = {}
    return [send_message(chat_id, text), menu_action(chat_id, employee)]


def _data_source(data: dict[str, Any]) -> DataSource:
    if not data.get("ai"):
        return DataSource.MANUAL
    return DataSource.MIXED if data.get("ai_edited") else DataSource.AI


def cancel(session: BotSession, employee: Employee, chat_id: int) -> list[Action]:
    """Descarta el borrador: como nada se guardó aún, no queda ningún registro a medias."""
    session.state = MENU
    session.data = {}
    session.draft_expense_id = None
    return [send_message(chat_id, msg.EXP_CANCELLED, remove_keyboard=True), menu_action(chat_id, employee)]


# --- Archivos (fotos de facturas y RUT) ---



def _on_file(db: Session, session: BotSession, employee: Employee, incoming: Incoming) -> list[Action]:
    chat = incoming.chat_id
    if session.state == RUT:
        purpose = RUT_FILE
    elif session.state == PHOTO or not _data(session).get("document_id"):
        purpose = INVOICE  # se acepta la foto aunque llegue en otro paso
    else:
        return [send_message(chat, msg.EXP_ALREADY_HAVE_PHOTO), *prompt(db, session, employee, chat)]
    try:
        return inputs.request_file(incoming, purpose)
    except InvalidInput as error:
        return [send_message(chat, error.message), *prompt(db, session, employee, chat)]


def start_with_file(db: Session, session: BotSession, employee: Employee, incoming: Incoming) -> list[Action]:
    """El empleado envió la foto desde el menú: se guarda y se inicia el registro con ella.
    La pregunta de viáticos se hace igual (después de recibir la foto)."""
    session.data = {"flow": "expense"}
    session.draft_expense_id = None
    session.state = WHICH_MONEY if _open_viaticos(db, employee) else HAD_MONEY
    try:
        return inputs.request_file(incoming, INVOICE)
    except InvalidInput as error:
        return [send_message(incoming.chat_id, error.message), *prompt(db, session, employee, incoming.chat_id)]


def on_file_stored(
    db: Session, session: BotSession, employee: Employee, chat_id: int,
    document: Document, purpose: str,
    storage: Storage | None = None, vision: VisionModel | None = None,
) -> list[Action]:
    """El archivo YA está guardado en documents. Se continúa la conversación.

    Si hay `vision` (la IA), la foto de la factura se lee antes de seguir. Sin ella
    (IA desactivada, p. ej. en las pruebas) se continúa con los datos manuales.
    """
    if not is_active(session):
        # Canceló mientras se descargaba: el archivo queda guardado igual (nunca se pierde).
        return [send_message(chat_id, msg.EXP_FILE_NO_FLOW), menu_action(chat_id, employee)]

    if purpose == RUT_FILE:
        _set(session, rut_document_id=str(document.id))
        if session.state == RUT:
            _advance(session, NIT)
        return [send_message(chat_id, msg.EXP_RUT_SAVED), *prompt(db, session, employee, chat_id)]

    if document.expense_id is not None:
        return [send_message(chat_id, msg.EXP_PHOTO_IN_OTHER_EXPENSE), *prompt(db, session, employee, chat_id)]
    has_gps = bool((document.exif or {}).get("gps"))
    note = msg.EXP_PHOTO_SAVED_GPS if has_gps else msg.EXP_PHOTO_SAVED

    if vision is not None and storage is not None:
        result = extraction.analyze_document(db, storage, document, vision)
        if result.outcome == ExtractionOutcome.NOT_INVOICE:
            # Escenario C: no se asocia, pero el documento y el intento quedan registrados.
            return [send_message(chat_id, msg.EXP_AI_NOT_INVOICE), *prompt(db, session, employee, chat_id)]
        if result.outcome == ExtractionOutcome.EXTRACTED:  # escenario A
            _set(session, document_id=str(document.id), document_gps=has_gps)
            _prefill(db, session, employee, result.invoice, str(result.extraction.id))
            if _data(session).get("editing"):  # cambió la foto desde ✏️ Editar
                _set(session, editing=False)
                session.state = CONFIRM
            elif session.state == PHOTO:
                session.state = _next_state(session, PHOTO)
            note = msg.EXP_AI_READ_GPS if has_gps else msg.EXP_AI_READ
            return [send_message(chat_id, note), *prompt(db, session, employee, chat_id)]
        # Escenario B (ilegible) o falla técnica: la foto queda guardada y se sigue a mano.
        note = msg.EXP_AI_UNREADABLE if result.outcome == ExtractionOutcome.UNREADABLE else msg.EXP_AI_FAILED
        if _data(session).get("ai"):
            _set(session, ai_edited=True)  # la foto nueva no confirma lo que leyó la IA antes

    _set(session, document_id=str(document.id), document_gps=has_gps)
    if session.state == PHOTO:
        _advance(session, PHOTO)
    return [send_message(chat_id, note), *prompt(db, session, employee, chat_id)]


def _prefill(db: Session, session: BotSession, employee: Employee, invoice: InvoiceData,
             extraction_id: str) -> None:
    """Escenario A: llena el borrador con lo que leyó la IA y marca qué pasos saltar."""
    skip = [TOTAL, UNIT_PRICE, NIT, INVOICE_NUMBER]  # la IA ya los revisó (aunque vengan vacíos)
    values: dict[str, Any] = {
        "ai": True, "ai_edited": False, "ai_extraction_id": extraction_id,
        "total": str(invoice.total), "unit_price": None,
        "items": [{"description": i.description, "quantity": str(i.quantity),
                   "unit_price": str(i.unit_price) if i.unit_price is not None else None,
                   "line_total": str(i.line_total) if i.line_total is not None else None}
                  for i in invoice.items] or None,
        "subtotal": str(invoice.subtotal) if invoice.subtotal is not None else None,
        "tax_total": str(invoice.tax_total) if invoice.tax_total is not None else None,
        "issue_time": invoice.issue_time.isoformat() if invoice.issue_time else None,
        "cufe": invoice.cufe,
        "invoice_number": invoice.invoice_number,
        "supplier_nit": invoice.supplier_nit, "supplier_name": None, "ai_new_supplier": False,
    }
    if invoice.description:
        values["description"] = invoice.description
        skip.append(DESCRIPTION)
    if invoice.issue_date:
        values["issue_date"] = invoice.issue_date.isoformat()
        skip.append(ISSUE_DATE)
    if invoice.supplier_nit:
        number = invoice.supplier_nit.split("-")[0]
        known = db.scalar(select(Supplier).where(Supplier.company_id == employee.company_id, Supplier.nit == number))
        if known is not None and not known.name.startswith("NIT "):
            values["supplier_name"] = known.name  # el nombre registrado manda sobre el leído
        else:
            values["supplier_name"] = invoice.supplier_name
            values["ai_new_supplier"] = True
    if invoice.category:
        category = next((c for c in catalog.list_categories(db, employee.company_id) if c.name == invoice.category), None)
        if category is not None:
            values.update(category_id=str(category.id), category_name=category.name)
            skip.append(CATEGORY)
    _set(session, **values, ai_skip=skip)
