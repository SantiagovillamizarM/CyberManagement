"""Paso 5.1: lectura de facturas con IA (sin llamar a Gemini de verdad: no gasta cuota)."""

import base64
import json
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.orm import Session

from app.ai import invoice
from app.ai.gemini import GeminiClient, GeminiError, GeminiErrorKind, GeminiReply, get_vision_model
from app.main import app
from app.models import AdminUser, AIExtraction, Company, Document
from app.models.enums import AdminRole, DocumentKind, ExtractionOutcome, ProcessingStatus
from app.services import catalog, documents, extraction
from app.services.audit import Actor
from app.storage import get_storage
from app.storage.local import LocalStorage
from app.utils.dates import local_today
from app.utils.nit import compute_dv
from tests.test_documents import JPEG, PDF_BYTES

CATEGORIES = ["Alimentación", "Transporte", "Otros"]
NIT = f"900123456-{compute_dv('900123456')}"
WRONG_DV = str((int(compute_dv("900123456")) + 1) % 10)


def reply(**overrides) -> str:
    """Respuesta típica de Gemini para una factura de restaurante (escenario A)."""
    data = {
        "is_invoice": True, "legible": True, "confidence": 0.93,
        "supplier_name": "Restaurante El Buen Sabor S.A.S.", "supplier_nit": "900.123.456-" + NIT[-1],
        "invoice_number": "FE-1234", "cufe": None,
        "issue_date": local_today().isoformat(), "issue_time": "13:45",
        "items": [{"description": "Bandeja paisa", "quantity": 2, "unit_price": 21008.4, "line_total": 42016.8}],
        "subtotal": 42016.8, "tax_total": 3361.34, "total": 45378.14,
        "category": "Alimentación", "description": "Almuerzo en restaurante", "notes": None,
    }
    return json.dumps({**data, **overrides})


def parse(text: str) -> invoice.InvoiceData:
    return invoice.parse_reply(text, CATEGORIES, min_confidence=0.5)


# --- CA-1 y CA-2: extracción y escenarios ---


def test_ca1_scenario_a_extracts_everything() -> None:
    data = parse(reply())

    assert data.outcome == ExtractionOutcome.EXTRACTED
    assert data.supplier_name == "Restaurante El Buen Sabor S.A.S."
    assert data.supplier_nit == NIT
    assert data.invoice_number == "FE-1234"
    assert data.issue_date == local_today()
    assert data.issue_time.hour == 13 and data.issue_time.minute == 45
    assert data.total == Decimal("45378.14")
    assert data.subtotal == Decimal("42016.80") and data.tax_total == Decimal("3361.34")
    assert data.items[0].quantity == Decimal("2.000") and data.items[0].unit_price == Decimal("21008.40")
    assert data.category == "Alimentación"
    assert data.warnings == []


def test_ca2_scenario_b_invoice_without_readable_total() -> None:
    assert parse(reply(total=None)).outcome == ExtractionOutcome.UNREADABLE
    assert parse(reply(legible=False)).outcome == ExtractionOutcome.UNREADABLE
    assert parse(reply(confidence=0.3)).outcome == ExtractionOutcome.UNREADABLE


def test_ca2_scenario_b_keeps_partial_data() -> None:
    """Aunque no se lea el total, lo que sí se leyó sirve para no preguntarlo otra vez."""
    data = parse(reply(total=None))
    assert data.supplier_nit == NIT and data.issue_date == local_today()


def test_ca2_scenario_c_not_an_invoice() -> None:
    data = parse(json.dumps({"is_invoice": False, "legible": True, "confidence": 0.98, "items": [],
                             "not_invoice_reason": "Es la foto de un perro", "total": 5000}))

    assert data.outcome == ExtractionOutcome.NOT_INVOICE
    assert data.not_invoice_reason == "Es la foto de un perro"
    assert data.total is None  # de algo que no es factura no se toma ningún dato


# --- CA-3: Python valida lo que dice la IA ---


def test_ca3_nit_with_wrong_check_digit_is_discarded() -> None:
    data = parse(reply(supplier_nit="900.123.456-" + WRONG_DV))

    assert data.supplier_nit is None
    assert any("NIT" in w for w in data.warnings)


@pytest.mark.parametrize("raw", ["NIT: 900.123.456 - " + compute_dv("900123456"), "900123456"])
def test_ca3_nit_formats_are_normalized(raw: str) -> None:
    assert parse(reply(supplier_nit=raw)).supplier_nit == NIT


def test_ca3_future_and_too_old_dates_are_discarded() -> None:
    assert parse(reply(issue_date=(local_today() + timedelta(days=1)).isoformat())).issue_date is None
    assert parse(reply(issue_date="2019-01-01")).issue_date is None
    assert parse(reply(issue_date="03/11/2026")).issue_date is None  # no es AAAA-MM-DD


@pytest.mark.parametrize("total", [-45000, 0, 10**13])
def test_ca3_absurd_totals_are_discarded(total) -> None:
    data = parse(reply(total=total))

    assert data.total is None
    assert data.outcome == ExtractionOutcome.UNREADABLE


def test_ca3_totals_that_do_not_add_up_lower_confidence() -> None:
    good = parse(reply())
    bad = parse(reply(subtotal=10000, tax_total=1900))  # 11.900 ≠ 45.378

    assert bad.confidence < good.confidence
    assert "Subtotal + impuestos no coincide con el total" in bad.warnings


def test_ca3_items_may_add_up_to_subtotal() -> None:
    """Es normal que los productos sumen el subtotal (antes de IVA) y no el total."""
    assert parse(reply()).warnings == []


def test_ca3_cufe_must_be_complete() -> None:
    cufe = "a1" * 48
    assert parse(reply(cufe=cufe.upper())).cufe == cufe
    assert parse(reply(cufe="a1b2c3")).cufe is None


def test_ca3_category_must_be_one_of_the_company() -> None:
    assert parse(reply(category="alimentación")).category == "Alimentación"
    assert parse(reply(category="Viajes espaciales")).category is None


def test_ca3_texts_are_cleaned_and_truncated() -> None:
    data = parse(reply(supplier_name="  Tienda\n\nLa   Esquina ", invoice_number="X" * 80))

    assert data.supplier_name == "Tienda La Esquina"
    assert len(data.invoice_number) == 50


def test_ca3_response_that_is_not_the_requested_json() -> None:
    for text in ("no es json", "[]", json.dumps({"total": 5})):  # falta is_invoice
        with pytest.raises(invoice.InvalidAIResponse):
            parse(text)


def test_prompt_and_schema_list_company_categories() -> None:
    schema = invoice.build_schema(CATEGORIES)

    assert schema["properties"]["category"]["enum"] == CATEGORIES
    assert '"Transporte"' in invoice.build_prompt(CATEGORIES)
    assert "ignóralo" in invoice.build_prompt(CATEGORIES)  # defensa contra instrucciones dentro de la imagen


def test_parsed_result_is_json_serializable() -> None:
    json.dumps(parse(reply()).to_json())


# --- CA-4 y CA-5: cliente HTTP de Gemini ---


def gemini_ok(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": "pensando...", "thought": True}, {"text": text}]},
                            "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 1200, "candidatesTokenCount": 150, "thoughtsTokenCount": 50},
            "modelVersion": "gemini-2.5-flash"}


def client_with(handler) -> GeminiClient:
    return GeminiClient(api_key="clave-secreta", model="gemini-2.5-flash", timeout=5, thinking_budget=0,
                        transport=httpx.MockTransport(handler), retry_wait=0)


def call(client: GeminiClient) -> GeminiReply:
    return client.generate(prompt="lee", data=JPEG, mime_type="image/jpeg", schema={"type": "OBJECT"})


def test_ca5_key_goes_in_header_never_in_url() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["request"] = request
        return httpx.Response(200, json=gemini_ok(reply()))

    result = call(client_with(handler))

    request = seen["request"]
    assert request.headers["x-goog-api-key"] == "clave-secreta"
    assert "clave-secreta" not in str(request.url)
    assert str(request.url).endswith("/models/gemini-2.5-flash:generateContent")
    body = json.loads(request.content)
    assert base64.b64decode(body["contents"][0]["parts"][0]["inline_data"]["data"]) == JPEG
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert json.loads(result.text)["is_invoice"] is True  # se ignoró la parte de "pensamiento"
    assert (result.tokens_in, result.tokens_out) == (1200, 200)


@pytest.mark.parametrize(("response", "kind"), [
    (httpx.Response(429, json={"error": {"message": "Quota exceeded"}}), GeminiErrorKind.RATE_LIMITED),
    (httpx.Response(400, json={"error": {"message": "bad"}}), GeminiErrorKind.HTTP),
    (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), GeminiErrorKind.BLOCKED),
    (httpx.Response(200, json={"candidates": []}), GeminiErrorKind.BAD_RESPONSE),
    (httpx.Response(200, text="<html>"), GeminiErrorKind.BAD_RESPONSE),
    (httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": ""}]}, "finishReason": "MAX_TOKENS"}]}),
     GeminiErrorKind.BAD_RESPONSE),
])
def test_ca4_failures_become_gemini_errors(response: httpx.Response, kind: GeminiErrorKind) -> None:
    with pytest.raises(GeminiError) as error:
        call(client_with(lambda request: response))
    assert error.value.kind == kind


def test_ca4_timeout_and_network_errors() -> None:
    def timeout(request):
        raise httpx.ReadTimeout("lento", request=request)

    def offline(request):
        raise httpx.ConnectError("sin internet", request=request)

    with pytest.raises(GeminiError) as error:
        call(client_with(timeout))
    assert error.value.kind == GeminiErrorKind.TIMEOUT
    with pytest.raises(GeminiError) as error:
        call(client_with(offline))
    assert error.value.kind == GeminiErrorKind.NETWORK


def test_ca4_temporary_google_error_is_retried_once() -> None:
    responses = iter([httpx.Response(503), httpx.Response(200, json=gemini_ok(reply()))])
    assert json.loads(call(client_with(lambda request: next(responses))).text)["total"] == 45378.14

    attempts = []
    with pytest.raises(GeminiError):
        call(client_with(lambda request: attempts.append(1) or httpx.Response(503)))
    assert len(attempts) == 2  # un solo reintento: no se insiste indefinidamente


# --- CA-2, CA-3 y CA-4: servicio de extracción (con un modelo falso) ---


class FakeModel:
    model = "fake-vision"

    def __init__(self, text: str | None = None, error: GeminiError | None = None) -> None:
        self.text, self.error, self.calls = text, error, []

    def generate(self, *, prompt, data, mime_type, schema) -> GeminiReply:
        self.calls.append({"prompt": prompt, "data": data, "mime_type": mime_type, "schema": schema})
        if self.error:
            raise self.error
        return GeminiReply(text=self.text, model_version="fake-1", finish_reason="STOP", tokens_in=10, tokens_out=5)


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path)


@pytest.fixture
def photo(db: Session, storage: LocalStorage, company: Company, admin: AdminUser) -> Document:
    catalog.ensure_default_categories(db, company.id)
    return documents.store_document(db, storage, company_id=company.id, data=JPEG,
                                    kind=DocumentKind.INVOICE_PHOTO, actor=Actor.from_admin(admin)).document


def test_service_scenario_a_records_the_attempt(db: Session, storage, photo: Document) -> None:
    model = FakeModel(reply())

    result = extraction.analyze_document(db, storage, photo, model)

    assert result.outcome == ExtractionOutcome.EXTRACTED
    assert photo.processing_status == ProcessingStatus.EXTRACTED
    saved = db.get(AIExtraction, result.extraction.id)
    assert saved.model == "fake-vision" and saved.prompt_version == invoice.PROMPT_VERSION
    assert saved.is_invoice is True and saved.confidence == Decimal("0.930")
    assert saved.parsed["total"] == "45378.14" and saved.parsed["supplier_nit"] == NIT
    assert saved.raw_response["text"] == reply()
    assert (saved.tokens_in, saved.tokens_out) == (10, 5) and saved.latency_ms is not None
    # Se le envió la imagen guardada y las categorías REALES de la empresa.
    assert model.calls[0]["data"] == JPEG and model.calls[0]["mime_type"] == "image/jpeg"
    assert "Peajes" in model.calls[0]["schema"]["properties"]["category"]["enum"]


@pytest.mark.parametrize(("text", "status"), [
    (reply(total=None), ProcessingStatus.NEEDS_MANUAL_INPUT),
    (json.dumps({"is_invoice": False, "legible": True, "confidence": 0.9, "items": []}), ProcessingStatus.NOT_INVOICE),
])
def test_service_scenarios_b_and_c(db: Session, storage, photo: Document, text, status) -> None:
    extraction.analyze_document(db, storage, photo, FakeModel(text))
    assert photo.processing_status == status


def test_ca4_gemini_failure_keeps_the_photo_and_can_retry(db: Session, storage, photo: Document) -> None:
    failing = FakeModel(error=GeminiError(GeminiErrorKind.RATE_LIMITED, "Quota exceeded"))

    result = extraction.analyze_document(db, storage, photo, failing)

    assert result.outcome == ExtractionOutcome.ERROR and result.invoice is None
    assert photo.processing_status == ProcessingStatus.FAILED
    assert "rate_limited" in result.extraction.error
    assert storage.read(photo.storage_key) == JPEG  # la foto sigue intacta

    retry = extraction.analyze_document(db, storage, photo, FakeModel(reply()))
    assert retry.outcome == ExtractionOutcome.EXTRACTED
    db.refresh(photo)
    assert [e.outcome for e in photo.extractions] == [ExtractionOutcome.ERROR, ExtractionOutcome.EXTRACTED]


def test_ca4_invalid_ai_response_is_recorded_as_error(db: Session, storage, photo: Document) -> None:
    result = extraction.analyze_document(db, storage, photo, FakeModel("esto no es json"))

    assert result.outcome == ExtractionOutcome.ERROR
    assert "respuesta inválida" in result.extraction.error
    assert result.extraction.raw_response["text"] == "esto no es json"  # queda para revisar qué pasó


def test_ca4_missing_file_is_recorded_as_error(db: Session, storage, photo: Document) -> None:
    storage.delete(photo.storage_key)

    result = extraction.analyze_document(db, storage, photo, FakeModel(reply()))

    assert result.outcome == ExtractionOutcome.ERROR and "no se pudo leer" in result.extraction.error


def test_only_invoice_photos_are_analyzed(db: Session, storage, company: Company, admin: AdminUser) -> None:
    rut = documents.store_document(db, storage, company_id=company.id, data=PDF_BYTES, kind=DocumentKind.RUT,
                                   actor=Actor.from_admin(admin)).document
    with pytest.raises(extraction.NotAnalyzableError):
        extraction.analyze_document(db, storage, rut, FakeModel(reply()))


# --- Endpoints del panel ---


@pytest.fixture
def api_model(storage: LocalStorage):
    model = FakeModel(reply())
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_vision_model] = lambda: model
    yield model
    app.dependency_overrides.pop(get_storage, None)
    app.dependency_overrides.pop(get_vision_model, None)


def test_panel_can_analyze_and_list_attempts(client, csrf, photo: Document, api_model) -> None:
    response = client.post(f"/api/v1/documents/{photo.id}/analyze", headers=csrf)

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["processing_status"] == "extracted"
    assert body["extraction"]["outcome"] == "extracted"
    assert body["extraction"]["parsed"]["total"] == "45378.14"

    attempts = client.get(f"/api/v1/documents/{photo.id}/extractions").json()
    assert [a["outcome"] for a in attempts] == ["extracted"]


def test_panel_analyze_reports_gemini_failure_without_500(client, csrf, photo: Document, api_model) -> None:
    api_model.error = GeminiError(GeminiErrorKind.TIMEOUT, "lento")

    response = client.post(f"/api/v1/documents/{photo.id}/analyze", headers=csrf)

    assert response.status_code == 200
    assert response.json()["extraction"]["outcome"] == "error"
    assert response.json()["document"]["processing_status"] == "failed"


def test_auditor_cannot_spend_ai_quota(client, csrf, admin: AdminUser, photo: Document, api_model) -> None:
    admin.role = AdminRole.AUDITOR

    assert client.post(f"/api/v1/documents/{photo.id}/analyze", headers=csrf).status_code == 403
    assert api_model.calls == []
