"""Reglas de autenticación de administradores del panel."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import (
    hash_password,
    hash_token,
    new_token,
    password_needs_rehash,
    verify_password,
)
from app.models import AdminSession, AdminUser, Company
from app.models.enums import AdminRole

logger = logging.getLogger(__name__)


class InvalidCredentialsError(Exception):
    """Login fallido. Siempre el mismo error, sea cual sea la causa real."""


class WeakPasswordError(ValueError):
    pass


@dataclass(frozen=True)
class NewSession:
    session: AdminSession
    token: str  # va en la cookie; nunca se guarda en la BD
    csrf_token: str


def _now() -> datetime:
    return datetime.now(UTC)


def authenticate(db: Session, email: str, password: str) -> AdminUser:
    settings = get_settings()
    now = _now()
    user = db.scalar(select(AdminUser).where(AdminUser.email == email.strip().lower()))

    # Se verifica la contraseña SIEMPRE (incluso si el usuario no existe) para que
    # el tiempo de respuesta no revele qué correos están registrados.
    password_ok = verify_password(password, user.password_hash if user else None)

    if user is None:
        logger.info("Login fallido: correo no registrado")
        raise InvalidCredentialsError

    if user.locked_until and user.locked_until > now:
        logger.warning("Login rechazado: cuenta bloqueada (admin_id=%s)", user.id)
        raise InvalidCredentialsError

    if not password_ok:
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.max_failed_logins:
            user.locked_until = now + timedelta(minutes=settings.lockout_minutes)
            user.failed_login_attempts = 0
            logger.warning("Cuenta bloqueada por intentos fallidos (admin_id=%s)", user.id)
        db.commit()
        raise InvalidCredentialsError

    if not user.is_active:
        logger.warning("Login rechazado: cuenta desactivada (admin_id=%s)", user.id)
        raise InvalidCredentialsError

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def create_session(
    db: Session, user: AdminUser, ip_address: str | None, user_agent: str | None
) -> NewSession:
    settings = get_settings()
    now = _now()
    token, csrf_token = new_token(), new_token()
    session = AdminSession(
        admin_user_id=user.id,
        token_hash=hash_token(token),
        csrf_token_hash=hash_token(csrf_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.session_absolute_hours),
        ip_address=ip_address,
        user_agent=(user_agent or "")[:500] or None,
    )
    db.add(session)
    db.flush()
    return NewSession(session=session, token=token, csrf_token=csrf_token)


def get_active_session(db: Session, token: str) -> AdminSession | None:
    """Devuelve la sesión si es válida y renueva su actividad; si no, None."""
    settings = get_settings()
    now = _now()
    session = db.scalar(select(AdminSession).where(AdminSession.token_hash == hash_token(token)))
    if session is None or session.revoked_at is not None:
        return None
    idle_limit = session.last_seen_at + timedelta(minutes=settings.session_idle_minutes)
    if now >= session.expires_at or now >= idle_limit or not session.admin_user.is_active:
        return None
    session.last_seen_at = now
    return session


def revoke_session(session: AdminSession) -> None:
    session.revoked_at = _now()


def validate_password_strength(password: str) -> None:
    minimum = get_settings().min_password_length
    if len(password) < minimum:
        raise WeakPasswordError(f"La contraseña debe tener al menos {minimum} caracteres")


def create_initial_admin(
    db: Session, email: str, password: str, company_name: str
) -> tuple[AdminUser, bool]:
    """Crea la empresa y el primer superadministrador. Si ya existe, no cambia nada.

    Devuelve (administrador, creado). Nunca sobrescribe una contraseña existente.
    """
    email = email.strip().lower()
    existing = db.scalar(select(AdminUser).where(AdminUser.email == email))
    if existing is not None:
        return existing, False

    validate_password_strength(password)
    company = db.scalar(select(Company).order_by(Company.created_at).limit(1))
    if company is None:
        company = Company(name=company_name)
        db.add(company)
        db.flush()

    admin = AdminUser(
        company_id=company.id,
        email=email,
        full_name="Administrador",
        password_hash=hash_password(password),
        role=AdminRole.SUPERADMIN,
    )
    db.add(admin)
    db.flush()
    return admin, True
