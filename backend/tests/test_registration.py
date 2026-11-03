"""Pruebas de las reglas de los códigos de registro."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AdminUser, AuditLog, BotSession, Employee, RegistrationAttempt, RegistrationCode
from app.models.enums import EmployeeStatus
from app.services import employees, registration
from app.services.audit import Actor
from app.services.registration import RedeemStatus

CODE_PATTERN = re.compile(r"^[A-Z]{1,6}-[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}$")
TG_USER, TG_CHAT = 555000111, 555000111


@pytest.fixture
def actor(admin: AdminUser) -> Actor:
    return Actor.from_admin(admin)


def redeem(db: Session, code: str, telegram_user_id: int = TG_USER):
    return registration.redeem_code(
        db, code, telegram_user_id=telegram_user_id, telegram_chat_id=TG_CHAT, telegram_username="jhon"
    )


# --- Formato ---


def test_code_has_expected_format(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)

    assert CODE_PATTERN.match(issued.code), issued.code
    assert issued.code.startswith("JHON-")
    assert issued.hint == f"JHON-…{issued.code[-4:]}"


@pytest.mark.parametrize(
    ("first_name", "prefix"),
    [("José María", "JOSE"), ("Ñandú", "NANDU"), ("Maximiliano", "MAXIMI"), ("123", "CM")],
)
def test_prefix_is_clean_ascii(first_name: str, prefix: str) -> None:
    assert registration._prefix_from_name(first_name) == prefix


def test_codes_are_random(db: Session, employee: Employee, actor: Actor) -> None:
    codes = set()
    for _ in range(20):
        codes.add(registration.generate_code(db, employee, actor).code)

    assert len(codes) == 20


def test_only_hash_is_stored(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)

    stored = db.scalars(select(RegistrationCode)).one()

    assert stored.code_hash != issued.code
    assert issued.code not in stored.code_hint
    assert len(stored.code_hash) == 64


def test_code_expires_in_72_hours(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)

    remaining = issued.expires_at - datetime.now(UTC)

    assert timedelta(hours=71, minutes=59) < remaining <= timedelta(hours=72)


# --- Canje ---


def test_valid_code_links_telegram(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)

    result = redeem(db, issued.code)

    assert result.status == RedeemStatus.LINKED
    assert employee.telegram_user_id == TG_USER
    assert employee.telegram_username == "jhon"
    assert employee.linked_at is not None
    assert db.scalars(select(RegistrationCode)).one().used_at is not None


@pytest.mark.parametrize("transform", [str.lower, lambda c: c.replace("-", " "), lambda c: f"  {c}  "])
def test_redeem_tolerates_human_typing(db: Session, employee: Employee, actor: Actor, transform) -> None:
    issued = registration.generate_code(db, employee, actor)

    assert redeem(db, transform(issued.code)).status == RedeemStatus.LINKED


def test_redeem_tolerates_letter_o_instead_of_zero(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    while "0" not in issued.code[5:]:  # necesitamos un código que contenga un cero
        issued = registration.generate_code(db, employee, actor)

    assert redeem(db, issued.code[:5] + issued.code[5:].replace("0", "O")).status == RedeemStatus.LINKED


def test_code_works_only_once(db: Session, company, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    redeem(db, issued.code)

    result = redeem(db, issued.code, telegram_user_id=999)

    assert result.status == RedeemStatus.INVALID_CODE


def test_wrong_code_is_rejected(db: Session, employee: Employee, actor: Actor) -> None:
    registration.generate_code(db, employee, actor)

    assert redeem(db, "JHON-0000-0000").status == RedeemStatus.INVALID_CODE
    assert employee.telegram_user_id is None


def test_expired_code_is_rejected(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    db.scalars(select(RegistrationCode)).one().expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert redeem(db, issued.code).status == RedeemStatus.INVALID_CODE


def test_new_code_revokes_previous(db: Session, employee: Employee, actor: Actor) -> None:
    first = registration.generate_code(db, employee, actor)
    second = registration.generate_code(db, employee, actor)

    assert redeem(db, first.code).status == RedeemStatus.INVALID_CODE
    assert redeem(db, second.code).status == RedeemStatus.LINKED


def test_revoked_code_is_rejected(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    registration.revoke_active_code(db, employee, actor)

    assert redeem(db, issued.code).status == RedeemStatus.INVALID_CODE


def test_deactivating_employee_revokes_pending_code(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)

    employees.update_employee(db, employee, {"status": EmployeeStatus.INACTIVE}, actor)

    assert registration.active_code(db, employee) is None
    assert redeem(db, issued.code).status == RedeemStatus.INVALID_CODE


def test_inactive_employee_cannot_link(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    employee.status = EmployeeStatus.INACTIVE  # cambio directo, sin pasar por el servicio

    assert redeem(db, issued.code).status == RedeemStatus.EMPLOYEE_INACTIVE


def test_inactive_employee_cannot_receive_code(db: Session, employee: Employee, actor: Actor) -> None:
    employee.status = EmployeeStatus.INACTIVE

    with pytest.raises(registration.EmployeeNotEligibleError):
        registration.generate_code(db, employee, actor)


def test_telegram_account_already_linked(db: Session, company, employee: Employee, actor: Actor) -> None:
    redeem(db, registration.generate_code(db, employee, actor).code)
    other = Employee(company_id=company.id, first_name="Ana", last_name="Gómez", internal_id="EMP-002")
    db.add(other)
    db.flush()

    result = redeem(db, registration.generate_code(db, other, actor).code)

    assert result.status == RedeemStatus.TELEGRAM_ALREADY_LINKED
    assert other.telegram_user_id is None


def test_linked_employee_cannot_receive_new_code(db: Session, employee: Employee, actor: Actor) -> None:
    redeem(db, registration.generate_code(db, employee, actor).code)

    with pytest.raises(registration.EmployeeNotEligibleError):
        registration.generate_code(db, employee, actor)


# --- Fuerza bruta ---


def test_too_many_failures_block_even_valid_code(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    for _ in range(5):
        redeem(db, "JHON-0000-0000")

    assert redeem(db, issued.code).status == RedeemStatus.TOO_MANY_ATTEMPTS
    assert employee.telegram_user_id is None


def test_old_failures_do_not_count(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    long_ago = datetime.now(UTC) - timedelta(minutes=16)
    for _ in range(5):
        db.add(RegistrationAttempt(telegram_user_id=TG_USER, succeeded=False, created_at=long_ago))
    db.flush()

    assert redeem(db, issued.code).status == RedeemStatus.LINKED


def test_limit_is_per_telegram_account(db: Session, employee: Employee, actor: Actor) -> None:
    issued = registration.generate_code(db, employee, actor)
    for _ in range(5):
        redeem(db, "JHON-0000-0000", telegram_user_id=111)

    assert redeem(db, issued.code, telegram_user_id=222).status == RedeemStatus.LINKED


# --- Desvinculación y auditoría ---


def test_unlink_clears_telegram_and_bot_session(db: Session, employee: Employee, actor: Actor) -> None:
    redeem(db, registration.generate_code(db, employee, actor).code)
    db.add(BotSession(telegram_user_id=TG_USER, employee_id=employee.id))
    db.flush()

    assert registration.unlink_telegram(db, employee, actor)

    assert employee.telegram_user_id is None
    assert db.get(BotSession, TG_USER) is None
    registration.generate_code(db, employee, actor)  # ya puede recibir un código nuevo


def test_linking_is_audited(db: Session, employee: Employee, actor: Actor) -> None:
    redeem(db, registration.generate_code(db, employee, actor).code)

    entries = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()

    assert [e.action for e in entries] == ["create", "link_telegram"]
    # Los valores se guardan como JSON real, no como texto.
    assert entries[0].new_value["hint"].startswith("JHON-")
