"""Pruebas de login, sesiones, CSRF, bloqueo y roles."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import CSRF_HEADER, SESSION_COOKIE, require_roles
from app.core.security import hash_password, hash_token, verify_password
from app.main import app
from app.models import AdminSession, AdminUser, Company
from app.models.enums import AdminRole
from app.services.auth import WeakPasswordError, create_initial_admin
from tests.conftest import ADMIN_PASSWORD

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
LOGOUT = "/api/v1/auth/logout"
GENERIC_ERROR = "Correo o contraseña incorrectos"


# Endpoint de prueba para verificar el control de roles.
@app.get("/api/v1/_test/solo-superadmin")
def _superadmin_only(admin: AdminUser = Depends(require_roles(AdminRole.SUPERADMIN))) -> dict:
    return {"ok": True}


def login(client: TestClient, email: str = "admin@cybermanagement.com", password: str = ADMIN_PASSWORD):
    return client.post(LOGIN, json={"email": email, "password": password})


# --- Contraseñas ---


def test_password_hash_is_argon2id_and_not_plain_text() -> None:
    hashed = hash_password("mi-contraseña-secreta")

    assert hashed.startswith("$argon2id$")
    assert "mi-contraseña-secreta" not in hashed
    assert verify_password("mi-contraseña-secreta", hashed)
    assert not verify_password("otra-contraseña", hashed)


def test_same_password_produces_different_hashes() -> None:
    # Cada hash lleva una "sal" aleatoria: dos usuarios con la misma contraseña
    # no tienen el mismo hash.
    assert hash_password("igual") != hash_password("igual")


# --- Login ---


def test_login_success_sets_secure_cookies(client: TestClient, admin: AdminUser) -> None:
    response = login(client)

    assert response.status_code == 200
    body = response.json()
    assert body["admin"]["email"] == "admin@cybermanagement.com"
    assert "password_hash" not in body["admin"]
    set_cookie = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookie if c.startswith(f"{SESSION_COOKIE}="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie


def test_login_is_case_insensitive_for_email(client: TestClient, admin: AdminUser) -> None:
    assert login(client, email="  ADMIN@CyberManagement.com ").status_code == 200


def test_database_stores_only_token_hash(client: TestClient, admin: AdminUser, db: Session) -> None:
    login(client)
    token = client.cookies.get(SESSION_COOKIE)

    stored = db.scalars(select(AdminSession)).one()

    assert stored.token_hash == hash_token(token)
    assert stored.token_hash != token


@pytest.mark.parametrize(
    ("email", "password"),
    [
        ("admin@cybermanagement.com", "contraseña-incorrecta"),
        ("no-existe@cybermanagement.com", ADMIN_PASSWORD),
    ],
)
def test_wrong_credentials_give_same_generic_error(
    client: TestClient, admin: AdminUser, email: str, password: str
) -> None:
    response = login(client, email=email, password=password)

    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_ERROR


def test_inactive_admin_cannot_login(client: TestClient, admin: AdminUser) -> None:
    admin.is_active = False

    assert login(client).status_code == 401


# --- Bloqueo por fuerza bruta ---


def test_account_locks_after_five_failed_attempts(client: TestClient, admin: AdminUser) -> None:
    for _ in range(5):
        assert login(client, password="incorrecta").status_code == 401

    # Ni con la contraseña correcta puede entrar mientras está bloqueada.
    response = login(client)

    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_ERROR
    assert admin.locked_until is not None


def test_account_unlocks_after_lockout_period(client: TestClient, admin: AdminUser) -> None:
    admin.locked_until = datetime.now(UTC) - timedelta(seconds=1)

    assert login(client).status_code == 200


def test_successful_login_resets_failed_attempts(client: TestClient, admin: AdminUser) -> None:
    for _ in range(4):
        login(client, password="incorrecta")

    login(client)

    assert admin.failed_login_attempts == 0


# --- Sesión ---


def test_me_requires_session(client: TestClient) -> None:
    assert client.get(ME).status_code == 401


def test_me_returns_logged_admin(client: TestClient, admin: AdminUser) -> None:
    login(client)

    response = client.get(ME)

    assert response.status_code == 200
    assert response.json()["role"] == "superadmin"


def test_forged_session_token_is_rejected(client: TestClient, admin: AdminUser) -> None:
    client.cookies.set(SESSION_COOKIE, "token-inventado")

    assert client.get(ME).status_code == 401


def test_expired_session_is_rejected(client: TestClient, admin: AdminUser, db: Session) -> None:
    login(client)
    db.scalars(select(AdminSession)).one().expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert client.get(ME).status_code == 401


def test_idle_session_is_rejected(client: TestClient, admin: AdminUser, db: Session) -> None:
    login(client)
    db.scalars(select(AdminSession)).one().last_seen_at = datetime.now(UTC) - timedelta(hours=2)

    assert client.get(ME).status_code == 401


def test_deactivating_admin_ends_existing_sessions(client: TestClient, admin: AdminUser) -> None:
    login(client)
    admin.is_active = False

    assert client.get(ME).status_code == 401


# --- CSRF y logout ---


def test_logout_without_csrf_token_is_forbidden(client: TestClient, admin: AdminUser) -> None:
    login(client)

    response = client.post(LOGOUT)

    assert response.status_code == 403
    assert client.get(ME).status_code == 200  # la sesión sigue activa


def test_logout_with_wrong_csrf_token_is_forbidden(client: TestClient, admin: AdminUser) -> None:
    login(client)

    assert client.post(LOGOUT, headers={CSRF_HEADER: "falso"}).status_code == 403


def test_logout_with_csrf_token_revokes_session(client: TestClient, admin: AdminUser) -> None:
    csrf = login(client).json()["csrf_token"]
    token = client.cookies.get(SESSION_COOKIE)

    response = client.post(LOGOUT, headers={CSRF_HEADER: csrf})

    assert response.status_code == 204
    # Aunque alguien hubiera copiado la cookie, ya no sirve.
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get(ME).status_code == 401


# --- Roles ---


def test_role_is_enforced(client: TestClient, admin: AdminUser) -> None:
    login(client)
    assert client.get("/api/v1/_test/solo-superadmin").status_code == 200

    admin.role = AdminRole.AUDITOR

    assert client.get("/api/v1/_test/solo-superadmin").status_code == 403


# --- Cabeceras de seguridad ---


def test_security_headers_are_present(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


# --- Administrador inicial ---


def test_create_initial_admin_creates_company_and_superadmin(db: Session) -> None:
    admin, created = create_initial_admin(db, "Admin@CyberManagement.com", "x" * 16, "Mi Empresa")

    assert created
    assert admin.email == "admin@cybermanagement.com"
    assert admin.role == AdminRole.SUPERADMIN
    assert db.get(Company, admin.company_id).name == "Mi Empresa"


def test_create_initial_admin_never_overwrites_password(db: Session) -> None:
    first, _ = create_initial_admin(db, "admin@cybermanagement.com", "x" * 16, "Mi Empresa")
    original_hash = first.password_hash

    again, created = create_initial_admin(db, "admin@cybermanagement.com", "y" * 16, "Otra")

    assert not created
    assert again.password_hash == original_hash


def test_create_initial_admin_rejects_short_password(db: Session) -> None:
    with pytest.raises(WeakPasswordError):
        create_initial_admin(db, "admin@cybermanagement.com", "corta", "Mi Empresa")
