"""Paso 8.1: informes PDF (CA-1 a CA-7). Se lee el texto del PDF generado para comprobar las cifras."""

import io
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.main import app
from app.models import AdminUser, AuditLog, Company, Employee, Report
from app.models.enums import AdminRole, DocumentKind, ExpenseStatus, MovementSource
from app.reports import pdf as pdf_module
from app.reports.pdf import build_pdf
from app.services import catalog, documents, expenses, reports, viaticos
from app.services.audit import Actor
from app.storage import get_storage
from app.storage.local import LocalStorage
from app.utils.dates import local_today
from tests.test_documents import make_image

D = Decimal
TODAY = local_today()
START = TODAY - timedelta(days=10)


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_path)


@pytest.fixture
def api_storage(storage):
    app.dependency_overrides[get_storage] = lambda: storage
    yield storage
    app.dependency_overrides.pop(get_storage, None)


@pytest.fixture
def actor(admin: AdminUser) -> Actor:
    return Actor.from_admin(admin)


@pytest.fixture
def second(db: Session, company: Company) -> Employee:
    other = Employee(company_id=company.id, first_name="Ana", last_name="Ruiz", internal_id="EMP-002")
    db.add(other)
    db.flush()
    return other


def spend(db, employee, actor, total, *, day=TODAY, status=ExpenseStatus.SUBMITTED, category="Alimentación",
          description="Almuerzo con cliente", nit="800.197.268-4", name="Restaurante El Cliente", viatico=None, **extra):
    fields = {"description": description, "total": D(total), "issue_date": day, **extra}
    if category:
        fields["category_id"] = next(c.id for c in catalog.list_categories(db, employee.company_id) if c.name == category)
    if viatico:
        fields["viatico_id"] = viatico.id
    expense = expenses.create_expense(db, employee, fields, actor=actor, supplier_nit=nit, supplier_name=name)
    expense.status = status
    db.flush()
    return expense


def attach_photo(db, storage, employee, expense, actor, color="red"):
    doc = documents.store_document(db, storage, company_id=employee.company_id, data=make_image(color=color, size=(400, 600)),
                                   kind=DocumentKind.INVOICE_PHOTO, actor=actor, employee_id=employee.id).document
    documents.attach_to_expense(db, doc, expense, actor)
    return doc


@pytest.fixture
def data(db: Session, company: Company, employee: Employee, second: Employee, actor: Actor, storage):
    catalog.ensure_default_categories(db, company.id)
    lunch = spend(db, employee, actor, "45000", subtotal=D("37815"), tax_total=D("7185"))
    attach_photo(db, storage, employee, lunch, actor)
    spend(db, employee, actor, "38000", day=START, category="Transporte", description="Taxi aeropuerto",
          nit="900.123.456-8", name="Taxis Libres", status=ExpenseStatus.APPROVED)
    spend(db, second, actor, "420000", category="Hospedaje", description="Hotel 2 noches", nit=None, name=None,
          status=ExpenseStatus.IN_REVIEW)
    # Fuera del informe por defecto:
    spend(db, employee, actor, "999000", status=ExpenseStatus.REJECTED, description="Rechazado")
    spend(db, employee, actor, "777000", day=START - timedelta(days=1), description="Antes del período")


def filters(**kw) -> reports.ReportFilters:
    return reports.ReportFilters(date_from=START, date_to=TODAY, **kw)


def text_of(pdf: bytes) -> str:
    return "\n".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf)).pages)


# --- CA-1: filtros ---


def test_ca1_default_statuses_and_period(db: Session, company: Company, data) -> None:
    result = reports.collect(db, company.id, filters(), generated_by="Admin")

    assert sorted(r.expense.description for r in result.rows) == ["Almuerzo con cliente", "Hotel 2 noches", "Taxi aeropuerto"]
    assert result.total == D("503000")
    assert (result.subtotal, result.tax_total) == (D("37815"), D("7185"))
    assert result.rows[0].day == START  # en orden de fecha


def test_ca1_filters_by_employee_category_and_status(db: Session, company: Company, employee: Employee, data) -> None:
    transport = next(c for c in catalog.list_categories(db, company.id) if c.name == "Transporte")

    by_employee = reports.collect(db, company.id, filters(employee_id=employee.id), generated_by="A")
    by_category = reports.collect(db, company.id, filters(category_id=transport.id), generated_by="A")
    rejected = reports.collect(db, company.id, filters(statuses=(ExpenseStatus.REJECTED,)), generated_by="A")

    assert {r.employee_name for r in by_employee.rows} == {f"{employee.first_name} {employee.last_name}"}
    assert by_employee.employee_name == f"{employee.first_name} {employee.last_name}"
    assert [r.expense.description for r in by_category.rows] == ["Taxi aeropuerto"]
    assert [r.expense.description for r in rejected.rows] == ["Rechazado"]


def test_ca1_end_date_is_respected(db: Session, company: Company, data) -> None:
    """Un período que termina ayer no incluye los gastos de hoy."""
    result = reports.collect(db, company.id, reports.ReportFilters(date_from=START, date_to=TODAY - timedelta(days=1)),
                             generated_by="A")
    assert [r.expense.description for r in result.rows] == ["Taxi aeropuerto"]


@pytest.mark.parametrize(("start", "end", "message"), [
    (TODAY, TODAY - timedelta(days=1), "anterior"),
    (TODAY - timedelta(days=400), TODAY, "un año"),
])
def test_ca1_invalid_periods(db: Session, company: Company, start, end, message) -> None:
    with pytest.raises(reports.ReportRuleError, match=message):
        reports.collect(db, company.id, reports.ReportFilters(date_from=start, date_to=end), generated_by="A")


# --- CA-2 a CA-4: contenido del PDF ---


def test_ca2_pdf_has_header_summary_and_breakdowns(db: Session, company: Company, data, storage) -> None:
    text = text_of(build_pdf(reports.collect(db, company.id, filters(), generated_by="Laura Admin"), storage))

    assert company.name in text
    assert f"{START:%d/%m/%Y} al {TODAY:%d/%m/%Y}" in text
    assert "Generado el" in text and "por Laura Admin" in text
    for expected in ("Resumen general", "$503.000", "$37.815", "$7.185", "Por estado", "En revisión",
                     "Por tipo de gasto", "Hospedaje", "Por empleado", "Ana Ruiz", "$420.000"):
        assert expected in text, expected
    assert "Rechazado" not in text and "999.000" not in text  # no entra por defecto


def test_ca2_viaticos_section_with_settlement(db: Session, company: Company, employee: Employee, actor: Actor,
                                              storage, data) -> None:
    viatico = viaticos.open_viatico(db, employee, initial_amount=D("500000"), purpose="Viaje a Cali",
                                    source=MovementSource.ADMIN, actor=actor)
    spend(db, employee, actor, "120000", viatico=viatico, payment_source="viatico", description="Hotel Cali")

    result = reports.collect(db, company.id, filters(), generated_by="A")
    text = text_of(build_pdf(result, storage))

    assert result.viaticos_delivered == D("500000") and result.viaticos_remaining == D("380000")
    assert "Viáticos del período" in text and "Viaje a Cali" in text
    assert "$380.000" in text and "Debe reintegrar" in text


def test_ca3_expense_detail_and_page_numbers(db: Session, company: Company, data, storage) -> None:
    text = text_of(build_pdf(reports.collect(db, company.id, filters(), generated_by="A"), storage))

    for expected in ("Gastos individuales", "Restaurante El Cliente", "NIT 800197268-4", "Taxis Libres",
                     "Sin NIT", "Hotel 2 noches", "Total"):
        assert expected in text, expected
    pages = PdfReader(io.BytesIO(build_pdf(reports.collect(db, company.id, filters(), generated_by="A"), storage))).pages
    assert f"Página 1 de {len(pages)}" in pages[0].extract_text()
    assert f"Página {len(pages)} de {len(pages)}" in pages[-1].extract_text()


def test_ca4_photos_are_annexed_with_reference(db: Session, company: Company, data, storage) -> None:
    pdf = build_pdf(reports.collect(db, company.id, filters(), generated_by="A"), storage)
    text = text_of(pdf)

    assert "Anexos: fotos de las facturas" in text and "Anexo 1" in text and "A1" in text
    images = sum(len(page.images) for page in PdfReader(io.BytesIO(pdf)).pages)
    assert images == 1


def test_ca4_photos_can_be_left_out(db: Session, company: Company, data, storage) -> None:
    text = text_of(build_pdf(reports.collect(db, company.id, filters(include_photos=False), generated_by="A"), storage))
    assert "Anexos" not in text


def test_ca4_too_many_photos_are_limited(db: Session, company: Company, employee: Employee, actor: Actor,
                                         storage, data, monkeypatch) -> None:
    second = spend(db, employee, actor, "10000", description="Otra")
    attach_photo(db, storage, employee, second, actor, color="blue")
    monkeypatch.setattr(pdf_module, "MAX_PHOTOS", 1)

    text = text_of(build_pdf(reports.collect(db, company.id, filters(), generated_by="A"), storage))

    assert "Hay 1 foto(s) más que no se incluyeron" in text


def test_ca4_unreadable_photo_does_not_break_the_report(db: Session, company: Company, data, storage) -> None:
    result = reports.collect(db, company.id, filters(), generated_by="A")
    storage.delete(next(r for r in result.rows if r.photo).photo.storage_key)

    assert "No se pudo incluir esta foto" in text_of(build_pdf(result, storage))


# --- CA-5: textos de personas ---


def test_ca5_user_text_cannot_alter_the_pdf(db: Session, company: Company, employee: Employee, actor: Actor,
                                            storage) -> None:
    catalog.ensure_default_categories(db, company.id)
    employee.first_name = "<b>Jhon</b>"
    spend(db, employee, actor, "1000", description='<font size="60" color="red">GRANDE</font> & <unclosed',
          name="Tienda <i>X</i>")

    text = text_of(build_pdf(reports.collect(db, company.id, filters(), generated_by="<script>"), storage))

    assert '<font size="60" color="red">GRANDE</font> & <unclosed' in text.replace("\n", " ")
    assert "<b>Jhon</b>" in text and "Tienda <i>X</i>" in text and "por <script>" in text


# --- CA-6: guardar, auditar, ver y descargar ---


def test_ca6_generate_through_api(client, csrf, db: Session, admin: AdminUser, data, api_storage) -> None:
    response = client.post("/api/v1/reports", headers=csrf,
                           json={"date_from": START.isoformat(), "date_to": TODAY.isoformat()})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["summary"]["expenses"] == 3 and body["summary"]["total"] == "503000.00"
    assert body["summary"]["photos"] == 1
    assert body["filters"]["statuses"] == ["submitted", "in_review", "approved", "closed"]
    assert body["generated_by"] == admin.full_name

    report = db.get(Report, body["id"])
    assert api_storage.read(report.storage_key)[:5] == b"%PDF-" and report.size_bytes > 1000
    log = db.scalars(select(AuditLog).where(AuditLog.entity_type == "report")).one()
    assert log.action == "create" and log.actor_id == str(admin.id)

    listed = client.get("/api/v1/reports").json()
    assert [r["id"] for r in listed] == [body["id"]]


def test_ca6_preview_inline_and_download(client, csrf, data, api_storage) -> None:
    report = client.post("/api/v1/reports", headers=csrf,
                         json={"date_from": START.isoformat(), "date_to": TODAY.isoformat()}).json()

    preview = client.get(report["file_url"])
    download = client.get(report["file_url"] + "?download=true")

    assert preview.status_code == 200 and preview.headers["content-type"] == "application/pdf"
    assert preview.headers["content-disposition"].startswith("inline")
    assert preview.headers["x-frame-options"] == "SAMEORIGIN"  # solo el propio panel puede incrustarlo
    assert preview.headers["content-security-policy"] == "frame-ancestors 'self'"
    assert download.headers["content-disposition"].startswith("attachment")
    assert preview.content[:5] == b"%PDF-"


def test_ca6_other_pages_still_cannot_be_framed(client) -> None:
    assert client.get("/api/health").headers["x-frame-options"] == "DENY"
    assert client.get("/login.html").headers["x-frame-options"] == "DENY"


def test_ca6_auditor_can_generate(client, csrf, db: Session, admin: AdminUser, data, api_storage) -> None:
    admin.role = AdminRole.AUDITOR
    db.flush()
    response = client.post("/api/v1/reports", headers=csrf,
                           json={"date_from": START.isoformat(), "date_to": TODAY.isoformat()})
    assert response.status_code == 201


def test_ca6_invalid_period_is_422(client, csrf, api_storage) -> None:
    response = client.post("/api/v1/reports", headers=csrf,
                           json={"date_from": TODAY.isoformat(), "date_to": (TODAY - timedelta(days=3)).isoformat()})
    assert response.status_code == 422 and "anterior" in response.json()["detail"]


def test_ca6_report_of_other_company_is_404(client, csrf, db: Session, data, api_storage) -> None:
    report = client.post("/api/v1/reports", headers=csrf,
                         json={"date_from": START.isoformat(), "date_to": TODAY.isoformat()}).json()
    other = Company(name="Otra S.A.S.")
    db.add(other)
    db.flush()
    db.get(Report, report["id"]).company_id = other.id
    db.flush()

    assert client.get(report["file_url"]).status_code == 404


def test_ca6_requires_session(db: Session) -> None:
    from fastapi.testclient import TestClient
    assert TestClient(app).post("/api/v1/reports", json={}).status_code in (401, 403)


def test_empty_period_still_produces_a_pdf(db: Session, company: Company, storage) -> None:
    result = reports.collect(db, company.id, reports.ReportFilters(date_from=date(2026, 1, 1), date_to=date(2026, 1, 31)),
                             generated_by="A")
    assert "No hay facturas con estos filtros" in text_of(build_pdf(result, storage))
