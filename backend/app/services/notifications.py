"""Avisos por Telegram mediante una bandeja de salida ("transactional outbox").

    cambio de negocio + aviso ──(misma transacción)──► notifications (pending)
    n8n cada minuto ──► claim_due() ──► Telegram ──► report_result()

- Si el cambio falla (rollback), el aviso desaparece con él: nunca se avisa algo que no pasó.
- Si n8n o Telegram están caídos, el aviso espera y se reintenta con espera creciente.
- claim_due() bloquea las filas (FOR UPDATE SKIP LOCKED): dos ejecuciones de n8n al mismo
  tiempo nunca toman el mismo aviso.

Python decide QUÉ y A QUIÉN se avisa (reglas); n8n solo los entrega.
"""

import html
import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.bot import messages as msg
from app.core.config import get_settings
from app.models import BotSession, Document, Employee, Expense, Notification, Viatico
from app.models.enums import DocumentKind, EmployeeStatus, ExpenseStatus, MovementType, NotificationStatus, ProcessingStatus
from app.utils.dates import local_today
from app.utils.money import format_cop

logger = logging.getLogger(__name__)

RETRY_MINUTES = (1, 5, 15, 60)  # espera antes del 2.º, 3.º, 4.º y 5.º intento
LOCK_MINUTES = 5  # si n8n tomó un aviso y no reportó en este tiempo, se vuelve a ofrecer
PHOTO_REMINDER_MAX_AGE = timedelta(days=7)  # fotos más viejas ya no se recuerdan


def _now() -> datetime:
    return datetime.now(UTC)


# --- Crear avisos ---


def enqueue(
    db: Session, *, company_id: uuid.UUID, chat_id: int, kind: str, text: str,
    employee_id: uuid.UUID | None = None, dedup_key: str | None = None,
) -> Notification | None:
    """Agrega un aviso a la bandeja. Con `dedup_key`, si ya existe uno igual no se repite (devuelve None)."""
    if dedup_key and db.scalar(select(Notification.id).where(Notification.dedup_key == dedup_key)):
        return None
    notification = Notification(company_id=company_id, employee_id=employee_id, chat_id=chat_id,
                                kind=kind, text=text, dedup_key=dedup_key)
    try:
        with db.begin_nested():  # punto de guardado: un duplicado simultáneo no arruina la transacción
            db.add(notification)
            db.flush()
    except IntegrityError:
        return None
    return notification


def employee_chat(employee: Employee) -> int | None:
    """Chat de Telegram del empleado, o None si no se le puede avisar (sin vincular o desactivado)."""
    if employee.status != EmployeeStatus.ACTIVE or employee.deleted_at is not None:
        return None
    return employee.telegram_chat_id or employee.telegram_user_id


def notify_employee(db: Session, employee: Employee, kind: str, text: str,
                    dedup_key: str | None = None) -> Notification | None:
    chat = employee_chat(employee)
    if chat is None:
        return None
    return enqueue(db, company_id=employee.company_id, chat_id=chat, kind=kind, text=text,
                   employee_id=employee.id, dedup_key=dedup_key)


def notify_admin(db: Session, company_id: uuid.UUID, kind: str, text: str,
                 dedup_key: str | None = None) -> Notification | None:
    chat = get_settings().admin_telegram_chat_id
    if chat is None:
        return None  # no hay chat configurado para el administrador
    return enqueue(db, company_id=company_id, chat_id=chat, kind=kind, text=text, dedup_key=dedup_key)


# --- Eventos de negocio ---


def _expense_fields(expense: Expense) -> dict[str, str]:
    when = expense.issue_date or expense.received_at.date()
    return {
        "description": html.escape((expense.description or "Sin descripción")[:60]),
        "total": format_cop(expense.total),
        "date": when.strftime("%d/%m/%Y"),
    }


def expense_reviewed(db: Session, expense: Expense) -> None:
    """Después de aprobar o rechazar un gasto."""
    employee = db.get(Employee, expense.employee_id)
    if expense.status == ExpenseStatus.APPROVED:
        text = msg.NOTIF_EXPENSE_APPROVED.format(**_expense_fields(expense))
    elif expense.status == ExpenseStatus.REJECTED:
        text = msg.NOTIF_EXPENSE_REJECTED.format(**_expense_fields(expense),
                                                 reason=html.escape(expense.rejection_reason or ""))
    else:
        return
    notify_employee(db, employee, f"expense_{expense.status.value}", text)


def _purpose(viatico: Viatico) -> str:
    return html.escape((viatico.purpose or "Viático")[:60])


def viatico_opened(db: Session, employee: Employee, viatico: Viatico, amount: Decimal) -> None:
    notify_employee(db, employee, "viatico_opened",
                    msg.NOTIF_VIATICO_OPENED.format(purpose=_purpose(viatico), amount=format_cop(amount)))


def viatico_movement(db: Session, employee: Employee, viatico: Viatico, *, type: MovementType,
                     amount: Decimal, note: str | None, remaining: Decimal) -> None:
    template = {
        MovementType.ADDITIONAL: msg.NOTIF_VIATICO_ADDITIONAL,
        MovementType.REFUND: msg.NOTIF_VIATICO_REFUND,
        MovementType.ADJUSTMENT: msg.NOTIF_VIATICO_ADJUSTMENT,
    }.get(type)
    if template is None:
        return
    text = template.format(purpose=_purpose(viatico), amount=format_cop(amount),
                           note=html.escape(note or ""), remaining=format_cop(remaining))
    notify_employee(db, employee, f"viatico_{type.value}", text)


def viatico_closed(db: Session, employee: Employee, viatico: Viatico, balance) -> None:
    settlement = msg.NOTIF_SETTLEMENT[balance.settlement.value].format(amount=format_cop(abs(balance.remaining)))
    text = msg.NOTIF_VIATICO_CLOSED.format(
        purpose=_purpose(viatico), delivered=format_cop(balance.delivered), spent=format_cop(balance.spent),
        refunded=format_cop(balance.refunded), settlement=settlement,
    )
    notify_employee(db, employee, "viatico_closed", text)


# --- Recordatorios (n8n los dispara una vez al día) ---


def run_reminders(db: Session, *, now: datetime | None = None) -> dict[str, int]:
    """Crea los recordatorios del día. Se puede ejecutar varias veces: cada uno sale una sola vez por día."""
    now = now or _now()
    today = local_today()
    idle = timedelta(hours=get_settings().reminder_idle_hours)
    counts = {"unfinished": 0, "photos": 0, "admin_pending": 0}

    # 1) Registro de gasto empezado y abandonado.
    stale = db.scalars(
        select(BotSession).where(BotSession.state.like("exp:%"), BotSession.updated_at < now - idle,
                                 BotSession.employee_id.is_not(None))
    )
    for session in stale:
        employee = db.get(Employee, session.employee_id)
        if employee and notify_employee(db, employee, "reminder_unfinished", msg.NOTIF_REMINDER_UNFINISHED,
                                        dedup_key=f"reminder_unfinished:{employee.id}:{today}"):
            counts["unfinished"] += 1

    # 2) Fotos de facturas guardadas que nunca quedaron en un gasto (no las que no eran factura).
    pending_photos = db.execute(
        select(Document.employee_id, func.count())
        .where(Document.kind == DocumentKind.INVOICE_PHOTO, Document.expense_id.is_(None),
               Document.deleted_at.is_(None), Document.employee_id.is_not(None),
               Document.processing_status != ProcessingStatus.NOT_INVOICE,
               Document.received_at < now - idle, Document.received_at > now - PHOTO_REMINDER_MAX_AGE)
        .group_by(Document.employee_id)
    ).all()
    for employee_id, count in pending_photos:
        employee = db.get(Employee, employee_id)
        if employee and notify_employee(db, employee, "reminder_photos",
                                        msg.NOTIF_REMINDER_PHOTOS.format(count=count),
                                        dedup_key=f"reminder_photos:{employee.id}:{today}"):
            counts["photos"] += 1

    # 3) Al administrador: gastos esperando revisión.
    pending = db.execute(
        select(Expense.company_id, func.count(), func.coalesce(func.sum(Expense.total), 0))
        .where(Expense.status.in_([ExpenseStatus.SUBMITTED, ExpenseStatus.IN_REVIEW]), Expense.deleted_at.is_(None))
        .group_by(Expense.company_id)
    ).all()
    for company_id, count, total in pending:
        text = msg.NOTIF_ADMIN_PENDING.format(count=count, total=format_cop(Decimal(total)))
        if notify_admin(db, company_id, "admin_pending_review", text,
                        dedup_key=f"admin_pending:{company_id}:{today}"):
            counts["admin_pending"] += 1
    return counts


# --- Entrega (la usa n8n) ---


def claim_due(db: Session, limit: int = 20, *, now: datetime | None = None) -> list[Notification]:
    """Toma los avisos listos para enviar y los marca como 'enviando'."""
    now = now or _now()
    due = list(db.scalars(
        select(Notification)
        .where(or_(
            (Notification.status == NotificationStatus.PENDING) & (Notification.next_attempt_at <= now),
            # n8n los tomó pero nunca reportó (se cayó a mitad): se vuelven a ofrecer.
            (Notification.status == NotificationStatus.SENDING) & (Notification.locked_until < now),
        ))
        .order_by(Notification.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    ))
    for notification in due:
        notification.status = NotificationStatus.SENDING
        notification.attempts += 1
        notification.locked_until = now + timedelta(minutes=LOCK_MINUTES)
    db.flush()
    return due


class NotificationNotFoundError(Exception):
    pass


def is_permanent_error(error_code: int | None, description: str | None) -> bool:
    """Errores de Telegram que no se arreglan reintentando (bot bloqueado, chat inexistente)."""
    text = (description or "").lower()
    return error_code == 403 or (error_code == 400 and ("chat not found" in text or "user not found" in text))


def report_result(
    db: Session, notification_id: uuid.UUID, *, ok: bool, error_code: int | None = None,
    description: str | None = None, now: datetime | None = None,
) -> Notification:
    """n8n informa cómo le fue. Es idempotente: un reporte repetido no cambia nada."""
    now = now or _now()
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise NotificationNotFoundError
    if notification.status != NotificationStatus.SENDING:
        return notification  # ya se había reportado
    notification.locked_until = None
    if ok:
        notification.status = NotificationStatus.SENT
        notification.sent_at = now
        notification.last_error = None
    else:
        notification.last_error = f"{error_code or ''} {description or 'error desconocido'}".strip()[:1000]
        attempts_left = notification.attempts < get_settings().notification_max_attempts
        if is_permanent_error(error_code, description) or not attempts_left:
            notification.status = NotificationStatus.FAILED
            logger.warning("Aviso %s no se pudo entregar: %s", notification.id, notification.last_error)
        else:
            wait = RETRY_MINUTES[min(notification.attempts - 1, len(RETRY_MINUTES) - 1)]
            notification.status = NotificationStatus.PENDING
            notification.next_attempt_at = now + timedelta(minutes=wait)
    db.flush()
    return notification


def as_action(notification: Notification) -> dict:
    """Mismo formato de acción que usa el bot: n8n la ejecuta igual."""
    return {"method": "sendMessage",
            "payload": {"chat_id": notification.chat_id, "text": notification.text, "parse_mode": "HTML"}}
