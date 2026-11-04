"""Paso 6.1: avisos por Telegram con bandeja de salida (CA-1 a CA-7)."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AdminUser, BotSession, Company, Employee, Expense, Notification
from app.models.enums import (DocumentKind, EmployeeStatus, ExpenseStatus, MovementSource, MovementType,
                              NotificationStatus, ProcessingStatus)
from app.services import documents, expenses, notifications, viaticos
from app.services.audit import Actor
from tests.test_documents import make_image, storage  # noqa: F401

CHAT = 70001
ADMIN_CHAT = -100555  # p. ej. un grupo de Telegram


@pytest.fixture
def linked(db: Session, employee: Employee) -> Employee:
    employee.telegram_user_id = CHAT
    employee.telegram_chat_id = CHAT
    db.flush()
    return employee


@pytest.fixture
def actor(admin: AdminUser) -> Actor:
    return Actor.from_admin(admin)


@pytest.fixture
def admin_chat(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_telegram_chat_id", ADMIN_CHAT)


def submitted(db: Session, employee: Employee, actor: Actor, description="Almuerzo con cliente",
              total="45000") -> Expense:
    expense = expenses.create_expense(db, employee, {"description": description, "total": Decimal(total),
                                                     "issue_date": date(2026, 11, 3)}, actor=actor)
    return expenses.submit(db, expense, actor)


def outbox(db: Session) -> list[Notification]:
    return list(db.scalars(select(Notification).order_by(Notification.created_at)))


# --- CA-1: gastos aprobados y rechazados ---


def test_ca1_approved_expense_notifies_employee(db: Session, linked: Employee, actor: Actor) -> None:
    expense = submitted(db, linked, actor)

    expenses.approve(db, expense, actor)

    (notification,) = outbox(db)
    assert notification.chat_id == CHAT and notification.employee_id == linked.id
    assert notification.kind == "expense_approved"
    assert "Almuerzo con cliente" in notification.text and "$45.000" in notification.text
    assert "03/11/2026" in notification.text and "aprobado" in notification.text
    assert notification.status == NotificationStatus.PENDING


def test_ca1_rejection_includes_reason(db: Session, linked: Employee, actor: Actor) -> None:
    expense = submitted(db, linked, actor)

    expenses.reject(db, expense, actor, "Falta el NIT del proveedor")

    (notification,) = outbox(db)
    assert notification.kind == "expense_rejected"
    assert "Falta el NIT del proveedor" in notification.text and "Mis gastos" in notification.text


def test_ca6_user_text_is_html_escaped(db: Session, linked: Employee, actor: Actor) -> None:
    expense = submitted(db, linked, actor, description="<b>Taxi</b>")

    expenses.reject(db, expense, actor, "<a href='x'>clic</a> aquí")

    text = outbox(db)[0].text
    assert "&lt;b&gt;Taxi&lt;/b&gt;" in text and "&lt;a href=" in text


def test_ca6_no_notice_for_unlinked_or_inactive_employees(db: Session, employee: Employee, actor: Actor) -> None:
    expenses.approve(db, submitted(db, employee, actor), actor)  # sin Telegram vinculado
    assert outbox(db) == []

    employee.telegram_user_id = CHAT
    expense = submitted(db, employee, actor, description="Otro")
    employee.status = EmployeeStatus.INACTIVE
    expenses.approve(db, expense, actor)
    assert outbox(db) == []


# --- CA-2: viáticos ---


def test_ca2_viatico_lifecycle_notifies_with_balance(db: Session, linked: Employee, actor: Actor) -> None:
    viatico = viaticos.open_viatico(db, linked, initial_amount=Decimal("500000"), purpose="Viaje a Cali",
                                    source=MovementSource.ADMIN, actor=actor)
    viaticos.add_movement(db, viatico, type=MovementType.ADDITIONAL, amount=Decimal("100000"), note=None,
                          source=MovementSource.ADMIN, actor=actor)
    viaticos.add_movement(db, viatico, type=MovementType.ADJUSTMENT, amount=Decimal("-20000"),
                          note="Error de digitación", source=MovementSource.ADMIN, actor=actor)
    viaticos.close_viatico(db, viatico, actor)

    texts = {n.kind: n.text for n in outbox(db)}
    assert set(texts) == {"viatico_opened", "viatico_additional", "viatico_adjustment", "viatico_closed"}
    assert "Viaje a Cali" in texts["viatico_opened"] and "$500.000" in texts["viatico_opened"]
    assert "Disponible: <b>$600.000</b>" in texts["viatico_additional"]
    assert "Error de digitación" in texts["viatico_adjustment"] and "$580.000" in texts["viatico_adjustment"]
    assert "Te sobraron <b>$580.000</b>" in texts["viatico_closed"]


def test_ca2_viatico_declared_by_employee_in_bot_is_not_notified(db: Session, linked: Employee) -> None:
    viaticos.open_viatico(db, linked, initial_amount=Decimal("80000"), purpose="Caja menor",
                          source=MovementSource.EMPLOYEE, actor=Actor.from_employee(linked))
    assert outbox(db) == []


# --- CA-3: misma transacción que el cambio ---


def test_ca3_rolled_back_change_leaves_no_notice(db: Session, linked: Employee, actor: Actor) -> None:
    expense = submitted(db, linked, actor)
    savepoint = db.begin_nested()
    expenses.approve(db, expense, actor)
    assert len(outbox(db)) == 1

    savepoint.rollback()  # la aprobación no se guardó (p. ej. falló otra cosa)

    assert outbox(db) == []


# --- CA-4: entrega, reintentos y bloqueos ---


def pending_notice(db: Session, linked: Employee) -> Notification:
    return notifications.notify_employee(db, linked, "test", "Hola")


def test_ca4_claim_marks_sending_and_is_not_offered_twice(db: Session, linked: Employee) -> None:
    notice = pending_notice(db, linked)

    (claimed,) = notifications.claim_due(db)

    assert claimed.id == notice.id
    assert claimed.status == NotificationStatus.SENDING and claimed.attempts == 1
    assert notifications.claim_due(db) == []  # otra ejecución de n8n no lo vuelve a tomar
    assert notifications.as_action(claimed) == {
        "method": "sendMessage", "payload": {"chat_id": CHAT, "text": "Hola", "parse_mode": "HTML"}}


def test_ca4_notice_abandoned_by_n8n_is_offered_again(db: Session, linked: Employee) -> None:
    pending_notice(db, linked)
    notifications.claim_due(db)

    later = datetime.now(UTC) + timedelta(minutes=notifications.LOCK_MINUTES + 1)
    (again,) = notifications.claim_due(db, now=later)

    assert again.attempts == 2


def test_ca4_success(db: Session, linked: Employee) -> None:
    notice = pending_notice(db, linked)
    notifications.claim_due(db)

    notifications.report_result(db, notice.id, ok=True)

    assert notice.status == NotificationStatus.SENT and notice.sent_at is not None
    notifications.report_result(db, notice.id, ok=False, error_code=500)  # reporte repetido: no cambia nada
    assert notice.status == NotificationStatus.SENT


def test_ca4_temporary_failure_retries_with_growing_wait(db: Session, linked: Employee) -> None:
    notice = pending_notice(db, linked)
    now = datetime.now(UTC)
    waits = []
    for attempt in range(1, 5):
        assert notifications.claim_due(db, now=now) == [notice]
        notifications.report_result(db, notice.id, ok=False, error_code=502, description="Bad Gateway", now=now)
        assert notice.status == NotificationStatus.PENDING
        waits.append(round((notice.next_attempt_at - now).total_seconds() / 60))
        assert notifications.claim_due(db, now=now) == []  # aún no le toca
        now = notice.next_attempt_at

    assert waits == [1, 5, 15, 60]
    notifications.claim_due(db, now=now)
    notifications.report_result(db, notice.id, ok=False, error_code=502, now=now)  # 5.º intento
    assert notice.status == NotificationStatus.FAILED and "502" in notice.last_error


@pytest.mark.parametrize(("code", "description"), [
    (403, "Forbidden: bot was blocked by the user"), (400, "Bad Request: chat not found")])
def test_ca4_blocked_bot_is_not_retried(db: Session, linked: Employee, code, description) -> None:
    notice = pending_notice(db, linked)
    notifications.claim_due(db)

    notifications.report_result(db, notice.id, ok=False, error_code=code, description=description)

    assert notice.status == NotificationStatus.FAILED and notice.attempts == 1


# --- CA-5: recordatorios ---


def make_idle(db: Session, hours: int) -> None:
    db.execute(update(BotSession).values(updated_at=datetime.now(UTC) - timedelta(hours=hours)))


def test_ca5_unfinished_expense_is_reminded_once_a_day(db: Session, linked: Employee) -> None:
    db.add(BotSession(telegram_user_id=CHAT, employee_id=linked.id, state="exp:total", data={}))
    db.flush()
    make_idle(db, 13)

    assert notifications.run_reminders(db)["unfinished"] == 1
    assert notifications.run_reminders(db)["unfinished"] == 0  # misma fecha: no se repite
    (notice,) = outbox(db)
    assert notice.kind == "reminder_unfinished"


def test_ca5_recent_or_finished_conversations_are_not_reminded(db: Session, linked: Employee) -> None:
    db.add(BotSession(telegram_user_id=CHAT, employee_id=linked.id, state="exp:total", data={}))
    db.flush()
    make_idle(db, 2)  # escribió hace poco
    assert notifications.run_reminders(db)["unfinished"] == 0

    db.execute(update(BotSession).values(state="menu"))
    make_idle(db, 30)  # terminó: está en el menú
    assert notifications.run_reminders(db)["unfinished"] == 0


def test_ca5_unregistered_photos_are_reminded(db: Session, linked: Employee, company: Company, storage,
                                              actor: Actor) -> None:
    def photo(color, status):
        document = documents.store_document(db, storage, company_id=company.id, data=make_image(color=color),
                                            kind=DocumentKind.INVOICE_PHOTO, actor=actor, employee_id=linked.id).document
        document.processing_status = status
        document.received_at = datetime.now(UTC) - timedelta(days=1)
        return document

    photo("red", ProcessingStatus.EXTRACTED)
    photo("blue", ProcessingStatus.FAILED)
    photo("green", ProcessingStatus.NOT_INVOICE)  # no era una factura: no se recuerda
    old = photo("black", ProcessingStatus.EXTRACTED)
    old.received_at = datetime.now(UTC) - timedelta(days=10)  # muy vieja
    db.flush()

    assert notifications.run_reminders(db)["photos"] == 1
    assert "<b>2</b> foto(s)" in outbox(db)[0].text


def test_ca5_admin_gets_pending_review_summary(db: Session, linked: Employee, actor: Actor, admin_chat) -> None:
    submitted(db, linked, actor, total="45000")
    submitted(db, linked, actor, description="Taxi", total="12000")

    assert notifications.run_reminders(db)["admin_pending"] == 1
    notifications.run_reminders(db)

    (notice,) = outbox(db)
    assert notice.chat_id == ADMIN_CHAT and notice.employee_id is None
    assert "<b>2</b> gasto(s)" in notice.text and "$57.000" in notice.text


def test_ca5_admin_summary_needs_a_configured_chat(db: Session, linked: Employee, actor: Actor) -> None:
    submitted(db, linked, actor)
    assert notifications.run_reminders(db)["admin_pending"] == 0


def test_empty_admin_chat_in_env_means_not_configured(monkeypatch) -> None:
    from app.core.config import Settings
    monkeypatch.setenv("ADMIN_TELEGRAM_CHAT_ID", "")
    assert Settings().admin_telegram_chat_id is None


# --- CA-7: endpoints internos ---


@pytest.fixture
def internal() -> dict[str, str]:
    return {"X-Internal-Token": get_settings().internal_api_token.get_secret_value()}


def test_ca7_endpoints_require_internal_token(client) -> None:
    for path in ("/api/internal/notifications/claim", "/api/internal/reminders/run",
                 "/api/internal/notifications/00000000-0000-0000-0000-000000000000/result"):
        response = client.post(path, json={"ok": True}, headers={"X-Internal-Token": "falso"})
        assert response.status_code in (401, 403), path


def test_ca7_claim_and_report_through_api(client, db: Session, linked: Employee, internal) -> None:
    notice = pending_notice(db, linked)

    body = client.post("/api/internal/notifications/claim", headers=internal).json()

    assert body["notifications"] == [{"id": str(notice.id), "attempt": 1,
                                      "action": notifications.as_action(notice)}]
    response = client.post(f"/api/internal/notifications/{notice.id}/result", headers=internal,
                           json={"ok": False, "error_code": 403, "description": "bot was blocked by the user"})
    assert response.json() == {"id": str(notice.id), "status": "failed", "attempts": 1}
    assert client.post("/api/internal/notifications/00000000-0000-0000-0000-000000000000/result",
                       headers=internal, json={"ok": True}).status_code == 404


def test_ca7_reminders_through_api(client, internal) -> None:
    assert client.post("/api/internal/reminders/run", headers=internal).json() == {
        "unfinished": 0, "photos": 0, "admin_pending": 0}
