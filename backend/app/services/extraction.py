"""Análisis de un documento ya guardado con la IA (escenarios A, B y C).

REGLA FUNDAMENTAL: el documento YA está en disco y en la BD antes de llegar aquí.
Pase lo que pase con Gemini, la foto no se pierde: como mucho queda en `failed`
y se puede reintentar.

Cada intento —también los fallidos— queda en ai_extractions (trazabilidad).
El llamador hace commit.
"""

import logging
import time
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ai.gemini import GeminiError, VisionModel
from app.ai.invoice import PROMPT_VERSION, InvalidAIResponse, InvoiceData, build_prompt, build_schema, parse_reply
from app.core.config import get_settings
from app.models import AIExtraction, Document
from app.models.enums import DocumentKind, ExtractionOutcome, ProcessingStatus
from app.services import catalog
from app.storage import Storage
from app.storage.base import StorageError

logger = logging.getLogger(__name__)

_STATUS = {
    ExtractionOutcome.EXTRACTED: ProcessingStatus.EXTRACTED,  # escenario A
    ExtractionOutcome.UNREADABLE: ProcessingStatus.NEEDS_MANUAL_INPUT,  # escenario B
    ExtractionOutcome.NOT_INVOICE: ProcessingStatus.NOT_INVOICE,  # escenario C
    ExtractionOutcome.ERROR: ProcessingStatus.FAILED,  # se puede reintentar
}


class NotAnalyzableError(Exception):
    """Ese documento no es una foto de factura (p. ej. un RUT)."""


@dataclass
class AnalysisResult:
    extraction: AIExtraction
    invoice: InvoiceData | None  # None si hubo un error técnico

    @property
    def outcome(self) -> ExtractionOutcome:
        return self.extraction.outcome


def analyze_document(db: Session, storage: Storage, document: Document, model: VisionModel) -> AnalysisResult:
    if document.kind != DocumentKind.INVOICE_PHOTO:
        raise NotAnalyzableError("Solo se analizan fotos de facturas")
    settings = get_settings()
    categories = [c.name for c in catalog.list_categories(db, document.company_id)]
    extraction = AIExtraction(document_id=document.id, model=model.model, prompt_version=PROMPT_VERSION,
                              outcome=ExtractionOutcome.ERROR)
    invoice: InvoiceData | None = None
    started = time.perf_counter()
    try:
        data = storage.read(document.storage_key)
        reply = model.generate(prompt=build_prompt(categories), data=data, mime_type=document.mime_type,
                               schema=build_schema(categories))
        extraction.raw_response = {"text": reply.text[:20000], "model_version": reply.model_version,
                                   "finish_reason": reply.finish_reason}
        extraction.tokens_in, extraction.tokens_out = reply.tokens_in, reply.tokens_out
        invoice = parse_reply(reply.text, categories, settings.ai_min_confidence)
    except GeminiError as exc:
        extraction.error = str(exc)[:1000]
        logger.warning("Gemini falló con el documento %s: %s", document.id, exc.kind)
    except InvalidAIResponse as exc:
        extraction.error = f"respuesta inválida: {exc}"[:1000]
        logger.warning("Gemini respondió algo inesperado para el documento %s", document.id)
    except StorageError as exc:
        extraction.error = f"no se pudo leer el archivo: {exc}"[:1000]
        logger.error("No se pudo leer el documento %s del almacenamiento", document.id)
    extraction.latency_ms = int((time.perf_counter() - started) * 1000)

    if invoice is not None:
        extraction.outcome = invoice.outcome
        extraction.is_invoice = invoice.is_invoice
        extraction.confidence = invoice.confidence
        extraction.parsed = invoice.to_json()
    document.processing_status = _STATUS[extraction.outcome]
    db.add(extraction)
    db.flush()
    return AnalysisResult(extraction, invoice)
