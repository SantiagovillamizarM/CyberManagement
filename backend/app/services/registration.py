"""Códigos de registro: generación, revocación y canje desde Telegram.

Formato: NOMBRE-XXXX-XXXX (ej. JHON-7KQ4-M9XT).
- La parte aleatoria usa el alfabeto Crockford Base32 (sin I, L, O, U, que se
  confunden con 1, 1, 0 y V): 8 caracteres = 40 bits ≈ 1,1 billones de combinaciones.
- Se guarda solo HMAC-SHA256(código, pepper): sin el pepper del .env, un respaldo
  robado de la base de datos no permite reconstruir ni probar códigos.
"""

import hashlib
import hmac
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import BotSession, Employee, RegistrationAttempt, RegistrationCode
from app.models.enums import EmployeeStatus
from app.services import audit
from app.services.audit import Actor

logger = logging.getLogger(__name__)

CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
PREFIX_MAX_LENGTH = 6
DEFAULT_PREFIX = "CM"


class EmployeeNotEligibleError(Exception):
    """El empleado no puede recibir un código (inactivo o ya vinculado)."""


@dataclass(frozen=True)
class IssuedCode:
    code: str  # solo existe en memoria: se muestra una vez al administrador
    hint: str
    expires_at: datetime


class RedeemStatus(StrEnum):
    LINKED = "linked"
    INVALID_CODE = "invalid_code"  # no existe, vencido, usado o revocado
    EMPLOYEE_INACTIVE = "employee_inactive"
    TELEGRAM_ALREADY_LINKED = "telegram_already_linked"  # esta cuenta ya está vinculada
    TOO_MANY_ATTEMPTS = "too_many_attempts"


@dataclass(frozen=True)
class RedeemResult:
    status: RedeemStatus
    employee: Employee | None = None


def _now() -> datetime:
    return datetime.now(UTC)


# --- Formato y hash ---


def _prefix_from_name(first_name: str) -> str:
    # "José María" -> "JOSE": sin tildes, solo letras, máximo 6.
    ascii_name = unicodedata.normalize("NFKD", first_name).encode("ascii", "ignore").decode()
    letters = re.sub(r"[^A-Za-z]", "", ascii_name.split(" ")[0] if ascii_name else "")
    return letters.upper()[:PREFIX_MAX_LENGTH] or DEFAULT_PREFIX


def _random_block(length: int = 4) -> str:
    return "".join(secrets.choice(CROCKFORD_ALPHABET) for _ in range(length))


def normalize_code(raw: str) -> str:
    """Tolera lo que un humano escribe: minúsculas, espacios, guiones, O por 0, I/L por 1."""
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw).upper()
    return cleaned.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))


def hash_code(raw: str) -> str:
    pepper = get_settings().registration_code_pepper.get_secret_value().encode()
    return hmac.new(pepper, normalize_code(raw).encode(), hashlib.sha256).hexdigest()


# --- Gestión desde el panel ---


def active_code(db: Session, employee: Employee) -> RegistrationCode | None:
    """Código pendiente (sin usar ni revocar). Puede estar vencido: revisar expires_at."""
    return db.scalar(
        select(RegistrationCode).where(
            RegistrationCode.employee_id == employee.id,
            RegistrationCode.used_at.is_(None),
            RegistrationCode.revoked_at.is_(None),
        )
    )


def revoke_active_code(db: Session, employee: Employee, actor: Actor) -> bool:
    code = active_code(db, employee)
    if code is None:
        return False
    code.revoked_at = _now()
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type="registration_code",
        entity_id=code.id,
        action="revoke",
        actor=actor,
        new_value={"employee_id": str(employee.id), "hint": code.code_hint},
    )
    db.flush()  # el índice único exige que el anterior quede revocado antes de crear otro
    return True


def generate_code(db: Session, employee: Employee, actor: Actor) -> IssuedCode:
    if employee.status != EmployeeStatus.ACTIVE or employee.deleted_at is not None:
        raise EmployeeNotEligibleError("El empleado está desactivado")
    if employee.telegram_user_id is not None:
        raise EmployeeNotEligibleError("El empleado ya tiene Telegram vinculado; desvincúlalo primero")

    revoke_active_code(db, employee, actor)

    random_part = _random_block() + _random_block()
    prefix = _prefix_from_name(employee.first_name)
    code = f"{prefix}-{random_part[:4]}-{random_part[4:]}"
    expires_at = _now() + timedelta(hours=get_settings().registration_code_hours)
    record = RegistrationCode(
        employee_id=employee.id,
        code_hash=hash_code(code),
        code_hint=f"{prefix}-…{random_part[-4:]}",
        created_by_id=actor.admin_id,
        expires_at=expires_at,
    )
    db.add(record)
    db.flush()
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type="registration_code",
        entity_id=record.id,
        action="create",
        actor=actor,
        new_value={"employee_id": str(employee.id), "hint": record.code_hint},
    )
    return IssuedCode(code=code, hint=record.code_hint, expires_at=expires_at)


# --- Canje desde Telegram ---


def _recent_failures(db: Session, telegram_user_id: int) -> int:
    window_start = _now() - timedelta(minutes=get_settings().registration_window_minutes)
    return db.scalar(
        select(func.count())
        .select_from(RegistrationAttempt)
        .where(
            RegistrationAttempt.telegram_user_id == telegram_user_id,
            RegistrationAttempt.succeeded.is_(False),
            RegistrationAttempt.created_at >= window_start,
        )
    )


def _fail(db: Session, telegram_user_id: int, status: RedeemStatus) -> RedeemResult:
    db.add(RegistrationAttempt(telegram_user_id=telegram_user_id, succeeded=False))
    logger.info("Canje de código fallido (telegram_user_id=%s, motivo=%s)", telegram_user_id, status)
    return RedeemResult(status)


def redeem_code(
    db: Session,
    raw_code: str,
    *,
    telegram_user_id: int,
    telegram_chat_id: int,
    telegram_username: str | None,
) -> RedeemResult:
    """Vincula la cuenta de Telegram al empleado dueño del código.

    No lanza excepciones por errores de negocio: devuelve un estado, para que el
    bot muestre el mensaje adecuado. El llamador debe hacer commit SIEMPRE
    (también en los fallos, para que cuenten los intentos).
    """
    if _recent_failures(db, telegram_user_id) >= get_settings().registration_max_failures:
        logger.warning("Demasiados intentos de registro (telegram_user_id=%s)", telegram_user_id)
        return RedeemResult(RedeemStatus.TOO_MANY_ATTEMPTS)

    linked = db.scalar(select(Employee).where(Employee.telegram_user_id == telegram_user_id))
    if linked is not None:
        return RedeemResult(RedeemStatus.TELEGRAM_ALREADY_LINKED, linked)

    # FOR UPDATE: si dos mensajes con el mismo código llegan a la vez, el segundo
    # espera al primero y luego ve el código ya usado.
    code = db.scalar(
        select(RegistrationCode)
        .where(RegistrationCode.code_hash == hash_code(raw_code))
        .with_for_update()
    )
    now = _now()
    if code is None or code.used_at or code.revoked_at or code.expires_at <= now:
        return _fail(db, telegram_user_id, RedeemStatus.INVALID_CODE)

    employee = code.employee
    if employee.status != EmployeeStatus.ACTIVE or employee.deleted_at is not None:
        return _fail(db, telegram_user_id, RedeemStatus.EMPLOYEE_INACTIVE)
    if employee.telegram_user_id is not None:
        return _fail(db, telegram_user_id, RedeemStatus.INVALID_CODE)

    employee.telegram_user_id = telegram_user_id
    employee.telegram_chat_id = telegram_chat_id
    employee.telegram_username = telegram_username
    employee.linked_at = now
    code.used_at = now
    code.used_by_telegram_user_id = telegram_user_id
    db.add(RegistrationAttempt(telegram_user_id=telegram_user_id, succeeded=True))
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type="employee",
        entity_id=employee.id,
        action="link_telegram",
        actor=Actor.from_employee(employee),
        field="telegram_user_id",
        new_value=telegram_user_id,
    )
    db.flush()
    logger.info("Telegram vinculado (employee_id=%s)", employee.id)
    return RedeemResult(RedeemStatus.LINKED, employee)


def unlink_telegram(db: Session, employee: Employee, actor: Actor) -> bool:
    if employee.telegram_user_id is None:
        return False
    old_telegram_id = employee.telegram_user_id
    db.execute(delete(BotSession).where(BotSession.telegram_user_id == old_telegram_id))
    employee.telegram_user_id = None
    employee.telegram_chat_id = None
    employee.telegram_username = None
    employee.linked_at = None
    audit.log(
        db,
        company_id=employee.company_id,
        entity_type="employee",
        entity_id=employee.id,
        action="unlink_telegram",
        actor=actor,
        field="telegram_user_id",
        old_value=old_telegram_id,
    )
    db.flush()
    return True
