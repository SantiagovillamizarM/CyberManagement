"""Dependencias compartidas por los endpoints del panel (autenticación y permisos)."""

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.security import tokens_match
from app.db.session import get_db
from app.models import AdminSession, AdminUser
from app.models.enums import AdminRole
from app.services import auth

SESSION_COOKIE = "cm_session"
CSRF_COOKIE = "cm_csrf"
CSRF_HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def get_current_session(request: Request, db: Session = Depends(get_db)) -> AdminSession:
    token = request.cookies.get(SESSION_COOKIE)
    session = auth.get_active_session(db, token) if token else None
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión inválida o expirada")

    # Toda acción que modifica datos exige el token CSRF en una cabecera. Otra web
    # puede hacer que el navegador envíe la cookie, pero no puede leer este token.
    if request.method not in SAFE_METHODS:
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or not tokens_match(sent, session.csrf_token_hash):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Token CSRF inválido")

    db.commit()  # guarda last_seen_at
    return session


def get_current_admin(session: AdminSession = Depends(get_current_session)) -> AdminUser:
    return session.admin_user


def require_roles(*roles: AdminRole) -> Callable[..., AdminUser]:
    """Uso: admin: AdminUser = Depends(require_roles(AdminRole.SUPERADMIN, AdminRole.ADMIN))"""

    def dependency(admin: AdminUser = Depends(get_current_admin)) -> AdminUser:
        if admin.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No tienes permiso para esta acción")
        return admin

    return dependency
