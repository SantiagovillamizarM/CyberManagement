"""Registra, consulta o elimina el webhook del bot en Telegram.

Uso (desde la carpeta backend):
    python -m scripts.telegram_webhook set     # usa N8N_WEBHOOK_URL del .env
    python -m scripts.telegram_webhook info
    python -m scripts.telegram_webhook delete

Nunca imprime el token del bot.
"""

import sys

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.config import PROJECT_ROOT

WEBHOOK_PATH = "webhook/telegram"  # ruta del nodo Webhook en el workflow CM-02
COMMANDS = [
    {"command": "start", "description": "Comenzar o volver al menú"},
    {"command": "menu", "description": "Ver el menú principal"},
    {"command": "saldo", "description": "Consultar mis viáticos"},
    {"command": "gastos", "description": "Ver mis últimos gastos"},
    {"command": "cancelar", "description": "Cancelar lo que estoy haciendo"},
    {"command": "ayuda", "description": "Ver la ayuda"},
]


class BotSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")
    telegram_bot_token: SecretStr
    telegram_webhook_secret: SecretStr
    n8n_webhook_url: str


def call(settings: BotSettings, method: str, payload: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/{method}"
    data = httpx.post(url, json=payload or {}, timeout=20).json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description')}")
    return data["result"]


def main(action: str) -> int:
    settings = BotSettings()
    if action == "set":
        base = settings.n8n_webhook_url.rstrip("/")
        if not base.startswith("https://"):
            print(f"[FALLA] N8N_WEBHOOK_URL debe ser HTTPS (actual: {base}). Ejecuta infra\\dev-tunnel.ps1")
            return 1
        url = f"{base}/{WEBHOOK_PATH}"
        call(settings, "setWebhook", {
            "url": url,
            "secret_token": settings.telegram_webhook_secret.get_secret_value(),
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,  # no procesar mensajes viejos acumulados
            "max_connections": 10,
        })
        call(settings, "setMyCommands", {"commands": COMMANDS, "language_code": "es"})
        call(settings, "setMyCommands", {"commands": COMMANDS})
        print(f"[OK] Webhook registrado: {url}")
        action = "info"

    if action == "info":
        info = call(settings, "getWebhookInfo")
        print(f"     URL:                 {info.get('url') or '(ninguna)'}")
        print(f"     Mensajes pendientes: {info.get('pending_update_count', 0)}")
        print(f"     Último error:        {info.get('last_error_message') or 'ninguno'}")
        print(f"     Actualizaciones:     {info.get('allowed_updates', 'todas')}")
        return 0

    if action == "delete":
        call(settings, "deleteWebhook", {"drop_pending_updates": True})
        print("[OK] Webhook eliminado")
        return 0

    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
