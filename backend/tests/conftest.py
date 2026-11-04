"""Configuración compartida de las pruebas.

Las pruebas usan una base de datos SEPARADA (cybermanagement_test). Cada prueba
corre dentro de una transacción que se deshace al final: la base queda limpia.
"""

import os

# Debe ejecutarse ANTES de importar la aplicación.
os.environ["APP_ENV"] = "test"
os.environ["POSTGRES_DB"] = "cybermanagement_test"

from collections.abc import Iterator  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import Connection  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from app.ai.gemini import get_vision_model  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import engine, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import AdminUser, Company, Employee  # noqa: E402
from app.models.enums import AdminRole  # noqa: E402

ADMIN_PASSWORD = "contraseña-de-prueba-segura"

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _create_test_database() -> None:
    settings = get_settings()
    admin_url = settings.database_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": settings.postgres_db}
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{settings.postgres_db}"'))
    admin_engine.dispose()


def alembic_config(connection: Connection) -> Config:
    config = Config(BACKEND_DIR / "alembic.ini")
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="session")
def database() -> Iterator[None]:
    """Crea la base de prueba desde cero aplicando las migraciones reales."""
    try:
        _create_test_database()
    except OperationalError:
        pytest.skip("PostgreSQL no está corriendo (docker compose up -d)")

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        config = alembic_config(conn)
        # Subir, bajar y volver a subir: comprueba que las migraciones son reversibles.
        command.upgrade(config, "head")
        command.downgrade(config, "base")
        command.upgrade(config, "head")
    yield


@pytest.fixture
def db(database: None) -> Iterator[Session]:
    """Sesión dentro de una transacción que se deshace al terminar la prueba."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# --- Fábricas de datos de prueba ---


@pytest.fixture
def company(db: Session) -> Company:
    company = Company(name="Empresa de Prueba S.A.S.", nit="900123456")
    db.add(company)
    db.flush()
    return company


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    """Cliente HTTP cuya API usa la sesión de prueba (todo se deshace al final)."""
    app.dependency_overrides[get_db] = lambda: db
    # Las pruebas NUNCA llaman a Gemini de verdad (no gastan cuota): IA desactivada por defecto.
    app.dependency_overrides[get_vision_model] = lambda: None
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def admin(db: Session, company: Company) -> AdminUser:
    admin = AdminUser(
        company_id=company.id,
        email="admin@cybermanagement.com",
        full_name="Administrador",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=AdminRole.SUPERADMIN,
    )
    db.add(admin)
    db.flush()
    return admin


@pytest.fixture
def csrf(client: TestClient, admin: AdminUser) -> dict[str, str]:
    """Inicia sesión como administrador y devuelve la cabecera CSRF lista para usar."""
    response = client.post(
        "/api/v1/auth/login", json={"email": admin.email, "password": ADMIN_PASSWORD}
    )
    return {"X-CSRF-Token": response.json()["csrf_token"]}


@pytest.fixture
def employee(db: Session, company: Company) -> Employee:
    employee = Employee(
        company_id=company.id, first_name="Jhon", last_name="Pérez", internal_id="EMP-001"
    )
    db.add(employee)
    db.flush()
    return employee
