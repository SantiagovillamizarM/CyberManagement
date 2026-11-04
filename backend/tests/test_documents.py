"""CA-1 a CA-12 del paso 3.5: archivos."""

import io
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import ExifTags, Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.main import app
from app.models import AdminUser, Company, Document, Employee, Expense
from app.models.enums import DocumentKind, LocationSource
from app.services import documents
from app.services.audit import Actor
from app.storage import get_storage
from app.storage.base import StorageError
from app.storage.inspection import (
    FileTooLargeError,
    InvalidFileError,
    UnsupportedFileError,
    inspect_file,
)
from app.storage.local import LocalStorage

# --- Archivos de prueba generados en memoria ---


def make_image(fmt: str = "JPEG", size=(120, 80), color="white", gps: dict | None = None) -> bytes:
    image = Image.new("RGB", size, color)
    buffer = io.BytesIO()
    kwargs = {}
    if gps is not None:
        exif = Image.Exif()
        exif[ExifTags.Base.Make] = "TestPhone"
        exif[ExifTags.IFD.GPSInfo] = gps
        kwargs["exif"] = exif
    image.save(buffer, fmt, **kwargs)
    return buffer.getvalue()


# Medellín: 6°14'39" N, 75°34'52" W
MEDELLIN_GPS = {
    ExifTags.GPS.GPSLatitudeRef: "N",
    ExifTags.GPS.GPSLatitude: (6.0, 14.0, 39.0),
    ExifTags.GPS.GPSLongitudeRef: "W",
    ExifTags.GPS.GPSLongitude: (75.0, 34.0, 52.0),
}
JPEG = make_image()
PDF_BYTES = b"%PDF-1.7\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path)


@pytest.fixture
def api_storage(storage: LocalStorage):
    app.dependency_overrides[get_storage] = lambda: storage
    yield storage
    app.dependency_overrides.pop(get_storage, None)


def stored_files(storage: LocalStorage) -> list[Path]:
    return [p for p in storage.root.rglob("*") if p.is_file()]


# --- CA-1, CA-2, CA-3: validación por contenido ---


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (make_image("JPEG"), "image/jpeg"),
        (make_image("PNG"), "image/png"),
        (make_image("WEBP"), "image/webp"),
        (PDF_BYTES, "application/pdf"),
    ],
)
def test_ca1_allowed_types(data: bytes, mime: str) -> None:
    assert inspect_file(data).mime_type == mime


@pytest.mark.parametrize(
    "data",
    [
        b"<html><script>alert(1)</script></html>",  # HTML disfrazado de .jpg
        b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 100,  # ejecutable de Windows
        make_image("GIF"),  # imagen real, pero tipo no permitido
    ],
)
def test_ca1_disguised_or_disallowed_files_are_rejected(data: bytes) -> None:
    with pytest.raises(UnsupportedFileError):
        inspect_file(data)


def test_ca2_empty_and_too_large() -> None:
    with pytest.raises(InvalidFileError):
        inspect_file(b"")
    with pytest.raises(FileTooLargeError):
        inspect_file(b"\xff\xd8" + b"x" * (get_settings().max_upload_mb * 1024 * 1024))


def test_ca3_truncated_image_is_rejected() -> None:
    big = make_image(size=(800, 600), color="red")
    with pytest.raises(InvalidFileError):
        inspect_file(big[: len(big) // 2])


@pytest.mark.parametrize("side", [8000, 11000])  # 64 MP (aviso) y 121 MP (error de Pillow)
def test_ca3_decompression_bomb_is_rejected(side: int) -> None:
    buffer = io.BytesIO()
    Image.new("1", (side, side)).save(buffer, "PNG")  # pocos KB en disco, enorme en memoria
    assert len(buffer.getvalue()) < 2 * 1024 * 1024

    with pytest.raises(InvalidFileError, match="píxeles"):
        inspect_file(buffer.getvalue())


# --- CA-6: GPS del EXIF ---


def test_ca6_gps_is_converted_to_decimal() -> None:
    info = inspect_file(make_image(gps=MEDELLIN_GPS))

    assert info.gps == (Decimal("6.244167"), Decimal("-75.581111"))
    assert info.exif["make"] == "TestPhone"


def test_ca6_southern_latitude_is_negative() -> None:
    gps = {**MEDELLIN_GPS, ExifTags.GPS.GPSLatitudeRef: "S"}

    assert inspect_file(make_image(gps=gps)).gps[0] == Decimal("-6.244167")


def test_ca6_no_gps_means_none() -> None:
    assert inspect_file(JPEG).gps is None


# --- CA-4: almacenamiento seguro ---


@pytest.mark.parametrize("key", ["../evil.jpg", "a/../../evil.jpg", "/etc/passwd", "C:/x.jpg", "a b.jpg", "x"])
def test_ca4_path_traversal_is_impossible(storage: LocalStorage, key: str) -> None:
    # Se prueba la resolución de la ruta, que NO escribe en disco: si algún día la
    # protección se rompiera, esta prueba fallaría sin crear archivos fuera de la carpeta.
    with pytest.raises(StorageError):
        storage._path(key)


def test_ca4_every_operation_uses_the_protected_path(storage: LocalStorage, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(storage, "_path", lambda key: calls.append(key) or (_ for _ in ()).throw(StorageError()))

    for operation in (lambda: storage.save("k.jpg", b"x"), lambda: storage.read("k.jpg"),
                      lambda: storage.delete("k.jpg"), lambda: storage.exists("k.jpg")):
        with pytest.raises(StorageError):
            operation()

    assert calls == ["k.jpg"] * 4


def test_ca4_random_name_and_original_name_only_as_metadata(
    db: Session, storage: LocalStorage, company: Company, admin: AdminUser
) -> None:
    stored = documents.store_document(
        db, storage, company_id=company.id, data=JPEG, kind=DocumentKind.INVOICE_PHOTO,
        actor=Actor.from_admin(admin), original_filename="../../factura <script>.jpg",
    )

    key = stored.document.storage_key
    assert key.startswith(f"{company.id}/") and key.endswith(f"{stored.document.id}.jpg")
    assert "factura" not in key
    assert stored.document.original_filename == "../../factura <script>.jpg"
    assert storage.read(key) == JPEG  # el original se guarda intacto


# --- CA-5, CA-7, CA-12 ---


def test_ca5_ca7_ca12_store_without_expense_with_hash_and_retention(
    db: Session, storage: LocalStorage, company: Company, admin: AdminUser
) -> None:
    actor = Actor.from_admin(admin)
    first = documents.store_document(db, storage, company_id=company.id, data=JPEG,
                                     kind=DocumentKind.INVOICE_PHOTO, actor=actor)
    again = documents.store_document(db, storage, company_id=company.id, data=JPEG,
                                     kind=DocumentKind.INVOICE_PHOTO, actor=actor)

    document = first.document
    assert first.created and not again.created
    assert again.document.id == document.id
    assert len(stored_files(storage)) == 1  # el duplicado no se volvió a guardar
    assert len(document.sha256) == 64
    assert document.expense_id is None  # CA-7: existe sin gasto
    assert document.processing_status == "received"
    received = document.received_at.date()
    assert document.retention_until == date(received.year + 10, received.month, received.day)


# --- CA-8: sin registros a medias ---


class FailingStorage(LocalStorage):
    def save(self, key: str, data: bytes) -> None:
        raise OSError("Disco lleno")


def test_ca8_disk_failure_creates_no_record(db: Session, tmp_path: Path, company: Company, admin) -> None:
    with pytest.raises(OSError):
        documents.store_document(db, FailingStorage(tmp_path), company_id=company.id, data=JPEG,
                                 kind=DocumentKind.INVOICE_PHOTO, actor=Actor.from_admin(admin))

    assert db.scalars(select(Document)).all() == []


def test_ca8_database_failure_removes_file(db: Session, storage: LocalStorage, company: Company, admin) -> None:
    with pytest.raises(Exception):
        documents.store_document(
            db, storage, company_id=company.id, data=JPEG, kind=DocumentKind.INVOICE_PHOTO,
            actor=Actor.from_admin(admin), employee_id=uuid.uuid4(),  # empleado inexistente: falla la FK
        )

    assert stored_files(storage) == []


# --- CA-9: descarga protegida ---


def upload(client: TestClient, csrf: dict, expense_id, data: bytes = JPEG, name: str = "factura.jpg"):
    return client.post(
        f"/api/v1/expenses/{expense_id}/documents",
        files={"file": (name, data, "image/jpeg")},
        headers=csrf,
    )


@pytest.fixture
def expense(db: Session, company: Company, employee: Employee) -> Expense:
    expense = Expense(company_id=company.id, employee_id=employee.id)
    db.add(expense)
    db.flush()
    return expense


def test_ca9_download_requires_session_and_has_safe_headers(
    client: TestClient, csrf: dict, api_storage, expense: Expense
) -> None:
    document_id = upload(client, csrf, expense.id).json()["document"]["id"]
    url = f"/api/v1/documents/{document_id}/file"

    response = client.get(url)

    assert response.status_code == 200
    assert response.content == JPEG
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in response.headers["content-security-policy"]
    assert response.headers["content-disposition"] == f'inline; filename="{document_id}.jpg"'

    client.cookies.clear()
    assert client.get(url).status_code == 401


def test_ca9_other_company_document_is_404(client: TestClient, csrf: dict, db: Session, storage) -> None:
    other = Company(name="Otra")
    db.add(other)
    db.flush()
    foreign = documents.store_document(db, storage, company_id=other.id, data=JPEG,
                                       kind=DocumentKind.INVOICE_PHOTO, actor=Actor(type="system"))

    assert client.get(f"/api/v1/documents/{foreign.document.id}").status_code == 404
    assert client.get(f"/api/v1/documents/{foreign.document.id}/file").status_code == 404


@pytest.mark.parametrize(
    ("data", "code"),
    [(b"<html>no soy una foto</html>", 415), (b"", 422), (b"\xff\xd8" + b"0" * (10 * 1024 * 1024), 413)],
)
def test_upload_errors_map_to_http(client, csrf, api_storage, expense, data, code) -> None:
    assert upload(client, csrf, expense.id, data=data).status_code == code
    assert stored_files(api_storage) == []


def test_upload_requires_csrf(client: TestClient, csrf: dict, api_storage, expense: Expense) -> None:
    assert upload(client, {}, expense.id).status_code == 403


# --- CA-11: ubicación desde EXIF al adjuntar ---


def test_ca11_exif_fills_missing_location(client, csrf, api_storage, expense: Expense, db: Session) -> None:
    body = upload(client, csrf, expense.id, data=make_image(gps=MEDELLIN_GPS)).json()

    assert body["document"]["gps"] == {"lat": "6.244167", "lon": "-75.581111"}
    db.refresh(expense)
    assert expense.location_source == LocationSource.EXIF
    assert expense.location_lat == Decimal("6.244167")


def test_ca11_exif_never_overrides_existing_location(client, csrf, api_storage, expense, db: Session) -> None:
    expense.location_lat, expense.location_lon = Decimal("4.6"), Decimal("-74.08")
    expense.location_source = LocationSource.MANUAL
    db.flush()

    upload(client, csrf, expense.id, data=make_image(gps=MEDELLIN_GPS))

    db.refresh(expense)
    assert expense.location_source == LocationSource.MANUAL
    assert expense.location_lat == Decimal("4.600000")


def test_same_photo_for_two_expenses_is_conflict(client, csrf, api_storage, expense, db, company, employee) -> None:
    other = Expense(company_id=company.id, employee_id=employee.id)
    db.add(other)
    db.flush()
    first = upload(client, csrf, expense.id)
    assert first.status_code == 201

    again = upload(client, csrf, expense.id)  # misma foto, mismo gasto: idempotente
    conflict = upload(client, csrf, other.id)  # misma foto, otro gasto: sospechoso

    assert again.status_code == 200 and again.json()["duplicate"] is True
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["existing_expense_id"] == str(expense.id)


# --- CA-10: endpoint interno para n8n ---

INTERNAL = "/api/internal/documents"


def internal_headers() -> dict[str, str]:
    return {"X-Internal-Token": get_settings().internal_api_token.get_secret_value()}


def internal_upload(client: TestClient, telegram_user_id: int, headers: dict | None = None):
    return client.post(
        INTERNAL,
        files={"file": ("photo.jpg", JPEG, "image/jpeg")},
        data={"telegram_user_id": str(telegram_user_id), "telegram_file_unique_id": "AQADxyz"},
        headers=internal_headers() if headers is None else headers,
    )


def test_ca10_internal_upload_requires_token(client: TestClient, api_storage) -> None:
    assert internal_upload(client, 1, headers={}).status_code == 401


def test_ca10_only_linked_active_employees(client: TestClient, api_storage, employee: Employee, db) -> None:
    assert internal_upload(client, 999).status_code == 403  # no vinculado

    employee.telegram_user_id = 999
    employee.status = "inactive"
    db.flush()
    assert internal_upload(client, 999).status_code == 403  # vinculado pero inactivo
    assert stored_files(api_storage) == []


def test_ca10_internal_upload_saves_photo_without_expense(client, api_storage, employee: Employee, db) -> None:
    employee.telegram_user_id = 999
    db.flush()

    response = internal_upload(client, 999)

    assert response.status_code == 201
    document = response.json()["document"]
    assert document["employee_id"] == str(employee.id)
    assert document["expense_id"] is None
    assert db.get(Document, uuid.UUID(document["id"])).telegram_file_unique_id == "AQADxyz"
