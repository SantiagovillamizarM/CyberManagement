"""Configuración central de la aplicación.

Todos los valores se leen de variables de entorno (o del archivo .env en la raíz
del proyecto). Si falta una variable obligatoria, la aplicación NO arranca: es
preferible fallar al inicio que descubrir el problema en producción.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

# backend/app/core/config.py -> raíz del proyecto (tres niveles arriba de app/)
PROJECT_ROOT = Path(__file__).resolve().parents[3]

MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",  # el .env también tiene variables de n8n y Telegram
    )

    # --- Aplicación ---
    app_name: str = "CyberManagement"
    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    timezone: str = "America/Bogota"

    # --- Secretos internos ---
    secret_key: SecretStr
    internal_api_token: SecretStr
    registration_code_pepper: SecretStr

    # --- Base de datos ---
    postgres_host: str = "127.0.0.1"  # no "localhost": en Windows intenta IPv6 primero
    postgres_port: int = 5432
    postgres_db: str
    postgres_user: str
    postgres_password: SecretStr

    # --- Inteligencia artificial ---
    gemini_api_key: SecretStr
    gemini_model: str = "gemini-2.5-flash"
    gemini_timeout_seconds: float = 45.0
    # 0 = sin "razonamiento" extra: responde más rápido (el empleado espera en el chat).
    gemini_thinking_budget: int | None = 0
    # Por debajo de esta confianza, la factura se trata como ilegible (escenario B).
    ai_min_confidence: float = 0.5

    # --- Telegram ---
    # Telegram lo envía en cada webhook (cabecera X-Telegram-Bot-Api-Secret-Token).
    # Prueba que el mensaje viene de Telegram y no de cualquiera que conozca la URL.
    # Ojo: el TOKEN del bot NO está aquí; solo lo usa n8n.
    telegram_webhook_secret: SecretStr
    # Versión de la política de tratamiento de datos que acepta el empleado (Ley 1581).
    data_policy_version: str = "2026-11-v1"

    # --- Notificaciones (Fase 6) ---
    # Chat de Telegram donde llegan los avisos para el administrador (su propio chat o un
    # grupo). Vacío = no se envían avisos al administrador.
    admin_telegram_chat_id: int | None = None
    notification_max_attempts: int = 5
    # Un registro de gasto sin terminar se recuerda tras estas horas sin actividad.
    reminder_idle_hours: int = 12

    # --- Panel web (HTML/CSS/JS). En desarrollo lo sirve la propia API. ---
    frontend_dir: Path = PROJECT_ROOT / "frontend"

    # --- Almacenamiento de archivos ---
    storage_dir: Path = PROJECT_ROOT / "storage"
    max_upload_mb: int = 10
    # Límite de píxeles al abrir una imagen: protege contra "bombas de descompresión".
    max_image_pixels: int = 50_000_000

    # --- Sesiones del panel ---
    session_absolute_hours: int = 8  # duración máxima de una sesión
    session_idle_minutes: int = 60  # se cierra tras este tiempo sin actividad
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    min_password_length: int = 12

    # --- Códigos de registro de empleados ---
    registration_code_hours: int = 72  # vigencia del código
    registration_max_failures: int = 5  # intentos fallidos permitidos...
    registration_window_minutes: int = 15  # ...en esta ventana de tiempo

    @field_validator(
        "secret_key", "internal_api_token", "registration_code_pepper", "telegram_webhook_secret"
    )
    @classmethod
    def _secret_must_be_long(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < MIN_SECRET_LENGTH:
            raise ValueError(f"debe tener al menos {MIN_SECRET_LENGTH} caracteres")
        return value

    @field_validator("admin_telegram_chat_id", mode="before")
    @classmethod
    def _empty_means_none(cls, value: object) -> object:
        # "ADMIN_TELEGRAM_CHAT_ID=" (vacío) en el .env significa "sin configurar".
        return None if isinstance(value, str) and not value.strip() else value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def cookie_secure(self) -> bool:
        # En producción las cookies solo viajan por HTTPS.
        return self.is_production

    @property
    def database_url(self) -> URL:
        # URL.create escapa correctamente caracteres especiales de la contraseña.
        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password.get_secret_value(),
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
