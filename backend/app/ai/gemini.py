"""Cliente de la API de Gemini (generateContent) con salida JSON según un esquema.

- La clave va en la cabecera x-goog-api-key, nunca en la URL (las URL terminan en registros).
- Cualquier falla (red, demora, cuota agotada, respuesta rara) se convierte en GeminiError:
  quien llama decide qué hacer, y la foto ya está guardada antes de llegar aquí.
"""

import base64
import logging
import time
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_STATUS = {500, 502, 503, 504}  # fallas temporales de Google: se reintenta una vez


class GeminiErrorKind(StrEnum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    RATE_LIMITED = "rate_limited"  # 429: se agotó la cuota gratuita (por minuto o por día)
    HTTP = "http"
    BLOCKED = "blocked"  # Gemini se negó a responder (filtros de seguridad)
    BAD_RESPONSE = "bad_response"


class GeminiError(Exception):
    def __init__(self, kind: GeminiErrorKind, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class GeminiReply:
    text: str  # el JSON que generó el modelo
    model_version: str | None
    finish_reason: str | None
    tokens_in: int | None
    tokens_out: int | None


class VisionModel(Protocol):
    """Lo que necesita el servicio de extracción. En las pruebas se usa uno falso."""

    model: str

    def generate(self, *, prompt: str, data: bytes, mime_type: str, schema: dict[str, Any]) -> GeminiReply: ...


class GeminiClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float,
        thinking_budget: int | None = None,
        transport: httpx.BaseTransport | None = None,
        retry_wait: float = 1.0,
    ) -> None:
        self.model = model
        self._thinking_budget = thinking_budget
        self._retry_wait = retry_wait
        self._http = httpx.Client(
            headers={"x-goog-api-key": api_key},
            timeout=httpx.Timeout(timeout, connect=10.0),
            transport=transport,
        )

    def generate(self, *, prompt: str, data: bytes, mime_type: str, schema: dict[str, Any]) -> GeminiReply:
        config: dict[str, Any] = {
            "responseMimeType": "application/json",
            "responseSchema": schema,
            "temperature": 0,  # extracción: siempre la misma respuesta para la misma imagen
        }
        if self._thinking_budget is not None:
            config["thinkingConfig"] = {"thinkingBudget": self._thinking_budget}
        body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode("ascii")}},
                    {"text": prompt},
                ],
            }],
            "generationConfig": config,
        }
        response = self._post(body)
        return self._parse(response)

    def _post(self, body: dict[str, Any]) -> httpx.Response:
        url = API_URL.format(model=self.model)
        for attempt in (1, 2):
            try:
                response = self._http.post(url, json=body)
            except httpx.TimeoutException as exc:
                raise GeminiError(GeminiErrorKind.TIMEOUT, "Gemini tardó demasiado en responder") from exc
            except httpx.HTTPError as exc:
                if attempt == 1:
                    time.sleep(self._retry_wait)
                    continue
                raise GeminiError(GeminiErrorKind.NETWORK, type(exc).__name__) from exc
            if response.status_code in RETRY_STATUS and attempt == 1:
                logger.warning("Gemini respondió %s; se reintenta", response.status_code)
                time.sleep(self._retry_wait)
                continue
            break
        if response.status_code == 429:
            raise GeminiError(GeminiErrorKind.RATE_LIMITED, _error_message(response))
        if response.status_code != 200:
            raise GeminiError(GeminiErrorKind.HTTP, f"HTTP {response.status_code}: {_error_message(response)}")
        return response

    def _parse(self, response: httpx.Response) -> GeminiReply:
        try:
            payload = response.json()
        except ValueError as exc:
            raise GeminiError(GeminiErrorKind.BAD_RESPONSE, "la respuesta no es JSON") from exc
        if block := (payload.get("promptFeedback") or {}).get("blockReason"):
            raise GeminiError(GeminiErrorKind.BLOCKED, str(block))
        candidates = payload.get("candidates") or []
        if not candidates:
            raise GeminiError(GeminiErrorKind.BAD_RESPONSE, "sin candidatos")
        candidate = candidates[0]
        finish = candidate.get("finishReason")
        parts = (candidate.get("content") or {}).get("parts") or []
        # Con "razonamiento" activo, algunas partes son pensamientos (thought=True): se ignoran.
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if finish in {"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII"}:
            raise GeminiError(GeminiErrorKind.BLOCKED, str(finish))
        if not text.strip():
            raise GeminiError(GeminiErrorKind.BAD_RESPONSE, f"respuesta vacía (finishReason={finish})")
        usage = payload.get("usageMetadata") or {}
        return GeminiReply(
            text=text,
            model_version=payload.get("modelVersion"),
            finish_reason=finish,
            tokens_in=usage.get("promptTokenCount"),
            tokens_out=(usage.get("candidatesTokenCount") or 0) + (usage.get("thoughtsTokenCount") or 0) or None,
        )


def _error_message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", ""))[:300]
    except ValueError:
        return response.text[:300]


@lru_cache
def get_vision_model() -> VisionModel:
    settings = get_settings()
    return GeminiClient(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.gemini_model,
        timeout=settings.gemini_timeout_seconds,
        thinking_budget=settings.gemini_thinking_budget,
    )
