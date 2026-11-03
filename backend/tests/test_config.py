import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID = {
    "secret_key": "s" * 32,
    "internal_api_token": "t" * 32,
    "registration_code_pepper": "p" * 32,
    "postgres_db": "db",
    "postgres_user": "user",
    "postgres_password": "p@ss/word",
    "gemini_api_key": "key",
}


def test_short_secret_is_rejected() -> None:
    with pytest.raises(ValidationError, match="al menos 32"):
        Settings(_env_file=None, **{**VALID, "secret_key": "corta"})


def test_database_url_escapes_password() -> None:
    settings = Settings(_env_file=None, **VALID)

    url = settings.database_url

    assert url.password == "p@ss/word"
    assert "p@ss/word" not in url.render_as_string(hide_password=True)
