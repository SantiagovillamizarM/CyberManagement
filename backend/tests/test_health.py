from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.db.session import engine, get_db
from app.main import app


class BrokenSession:
    """Simula una base de datos caída."""

    def execute(self, *args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("conexión rechazada"))


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_returns_503_when_database_is_down(client: TestClient) -> None:
    app.dependency_overrides[get_db] = lambda: BrokenSession()

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "error"}


def _database_available() -> bool:
    try:
        with engine.connect():
            return True
    except OperationalError:
        return False


@pytest.mark.skipif(not _database_available(), reason="PostgreSQL no está corriendo")
def test_health_ok_with_real_database(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}
