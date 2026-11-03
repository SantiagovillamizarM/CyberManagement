"""Verifica que PostgreSQL, Gemini y Telegram respondan con las credenciales del .env.

Uso (desde la carpeta backend, con el entorno virtual activo):
    python -m scripts.check_services

Nunca imprime las claves: solo indica si funcionan.
"""

import sys

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, text

from app.core.config import PROJECT_ROOT, get_settings

TIMEOUT = 15


class ExternalTokens(BaseSettings):
    """El token de Telegram no forma parte de la configuración de la API (lo usa n8n)."""

    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")
    telegram_bot_token: SecretStr


def check_postgres() -> str:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
    return f"PostgreSQL {version}"


def check_gemini() -> str:
    key = get_settings().gemini_api_key.get_secret_value()
    response = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key},  # en cabecera, no en la URL
        params={"pageSize": 100},
        timeout=TIMEOUT,
    )
    if response.status_code != 200:
        message = response.json().get("error", {}).get("message", response.text[:200])
        raise RuntimeError(f"HTTP {response.status_code}: {message}")
    flash = [
        m["name"].removeprefix("models/")
        for m in response.json().get("models", [])
        if "flash" in m["name"] and "generateContent" in m.get("supportedGenerationMethods", [])
    ]
    return "clave válida. Modelos Flash disponibles: " + ", ".join(flash[:8])


def check_telegram() -> str:
    token = ExternalTokens().telegram_bot_token.get_secret_value()
    response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=TIMEOUT)
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"HTTP {response.status_code}: {data.get('description')}")
    bot = data["result"]
    return f"bot @{bot['username']} ({bot['first_name']})"


def main() -> int:
    checks = {"PostgreSQL": check_postgres, "Gemini": check_gemini, "Telegram": check_telegram}
    failures = 0
    for name, check in checks.items():
        try:
            print(f"[OK]    {name}: {check()}")
        except Exception as exc:  # queremos reportar cualquier fallo y seguir
            failures += 1
            print(f"[FALLA] {name}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
