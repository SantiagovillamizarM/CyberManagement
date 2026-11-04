"""Paso 4.3: fotos y archivos desde Telegram (CA-1 a CA-10)."""

import itertools
import json
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.bot import engine, expense_flow as flow
from app.bot import messages as msg
from app.core.config import PROJECT_ROOT, get_settings
from app.models import BotSession, Document, Employee, Expense
from app.models.enums import DocumentKind, LocationSource
from app.storage.local import LocalStorage
from tests.test_bot import USER, text_update, texts
from tests.test_bot_expense import category_button, linked, location_update, say, state  # noqa: F401
from tests.test_documents import JPEG, MEDELLIN_GPS, PDF_BYTES, api_storage, make_image, storage  # noqa: F401

_ids = itertools.count(1)


def photo_update(file_size: int = 50_000) -> dict:
    update = text_update("x")
    del update["message"]["text"]
    n = next(_ids)
    update["message"]["photo"] = [  # Telegram manda varios tamaños; el último es el más grande
        {"file_id": f"small-{n}", "file_unique_id": f"us-{n}", "width": 90, "height": 60, "file_size": 1_000},
        {"file_id": f"big-{n}", "file_unique_id": f"ub-{n}", "width": 1280, "height": 960, "file_size": file_size},
    ]
    return update


def document_update(mime: str = "image/jpeg", name: str = "factura.jpg", file_size: int = 80_000) -> dict:
    update = text_update("x")
    del update["message"]["text"]
    n = next(_ids)
    update["message"]["document"] = {"file_id": f"doc-{n}", "file_unique_id": f"ud-{n}",
                                     "file_name": name, "mime_type": mime, "file_size": file_size}
    return update


def store_actions(actions: list[dict]) -> list[dict]:
    return [a for a in actions if a["method"] == "cm:store_file"]


def deliver(db: Session, storage: LocalStorage, data: bytes, purpose: str = flow.INVOICE,
            unique_id: str = "ub-x") -> list[dict]:
    """Simula a n8n: descargó el archivo de Telegram y lo sube a Python."""
    return engine.handle_file(db, storage, telegram_user_id=USER, purpose=purpose, data=data,
                              file_name="factura.jpg", file_unique_id=unique_id)


def to_photo_step(db: Session) -> None:
    say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))
    assert state(db) == flow.PHOTO


def finish_without_nit(db: Session, company, location=("btn", flow.CB_SKIP)) -> list[dict]:
    """Desde la descripción hasta el resumen, sin NIT."""
    return say(db, "Almuerzo", "45.000", ("btn", flow.CB_SKIP), ("btn", flow.CB_TODAY),
               ("btn", flow.CB_SKIP), ("btn", flow.CB_SKIP), category_button(db, company), location)


# --- CA-1 ---


def test_ca1_photo_is_requested_after_money_question(db: Session, linked: Employee) -> None:
    actions = say(db, ("btn", engine.CB_EXPENSE), ("btn", flow.CB_NO))

    assert texts(actions) == [msg.EXP_ASK_PHOTO]
    assert "archivo" in msg.EXP_ASK_PHOTO and "GPS" in msg.EXP_ASK_PHOTO
    assert actions[-1]["payload"]["reply_markup"]["inline_keyboard"][0][0]["callback_data"] == flow.CB_NO_PHOTO


def test_without_photo_goes_to_description(db: Session, linked: Employee) -> None:
    to_photo_step(db)

    assert texts(say(db, ("btn", flow.CB_NO_PHOTO))) == [msg.EXP_ASK_DESCRIPTION]


# --- CA-2: se pide descargar y se guarda antes de seguir ---


def test_ca2_photo_triggers_download_of_largest_size(db: Session, linked: Employee) -> None:
    to_photo_step(db)

    actions = engine.handle_update(db, photo_update())

    assert texts(actions) == [msg.EXP_RECEIVING]
    (request,) = store_actions(actions)
    assert request["payload"]["file_id"].startswith("big-")
    assert request["payload"]["purpose"] == flow.INVOICE
    assert request["payload"]["telegram_user_id"] == USER
    assert db.scalars(select(Document)).all() == []  # aún no ha llegado el archivo


def test_ca2_delivered_photo_is_stored_then_flow_continues(db, linked, storage) -> None:
    to_photo_step(db)
    engine.handle_update(db, photo_update())

    actions = deliver(db, storage, JPEG, unique_id="ub-777")

    document = db.scalars(select(Document)).one()
    assert document.kind == DocumentKind.INVOICE_PHOTO
    assert document.employee_id == linked.id
    assert document.telegram_file_unique_id == "ub-777"
    assert document.expense_id is None
    assert texts(actions) == [msg.EXP_PHOTO_SAVED, msg.EXP_ASK_DESCRIPTION]
    assert state(db) == flow.DESCRIPTION


# --- CA-3: la foto nunca se pierde ---


def test_ca3_photo_survives_cancel(db, linked, storage) -> None:
    to_photo_step(db)
    deliver(db, storage, JPEG)

    say(db, "/cancelar")

    assert db.scalars(select(Expense)).all() == []
    assert len(db.scalars(select(Document)).all()) == 1


def test_ca3_photo_arriving_after_cancel_is_still_stored(db, linked, storage) -> None:
    to_photo_step(db)
    engine.handle_update(db, photo_update())
    say(db, "/cancelar")  # canceló mientras se descargaba

    actions = deliver(db, storage, JPEG)

    assert texts(actions)[0] == msg.EXP_FILE_NO_FLOW
    assert len(db.scalars(select(Document)).all()) == 1


# --- CA-4: rechazos ---


def test_ca4_too_large_is_rejected_before_download(db: Session, linked: Employee) -> None:
    to_photo_step(db)

    actions = engine.handle_update(db, photo_update(file_size=11 * 1024 * 1024))

    assert store_actions(actions) == []
    assert texts(actions) == [msg.EXP_FILE_TOO_LARGE.format(mb=get_settings().max_upload_mb), msg.EXP_ASK_PHOTO]


def test_ca4_unsupported_type_is_rejected_before_download(db: Session, linked: Employee) -> None:
    to_photo_step(db)

    actions = engine.handle_update(db, document_update(mime="application/zip", name="facturas.zip"))

    assert store_actions(actions) == []
    assert texts(actions)[0] == msg.EXP_FILE_TYPE


def test_ca4_invalid_content_after_download(db, linked, storage) -> None:
    to_photo_step(db)
    engine.handle_update(db, document_update())

    actions = deliver(db, storage, b"<html>no soy una foto</html>")

    assert texts(actions)[0].startswith("⚠️ No pude usar ese archivo")
    assert texts(actions)[1] == msg.EXP_ASK_PHOTO
    assert db.scalars(select(Document)).all() == []
    assert state(db) == flow.PHOTO


# --- CA-5: al confirmar se asocia, con la ubicación del EXIF ---


def test_ca5_photo_with_gps_is_attached_and_sets_exif_location(db, linked, storage, company) -> None:
    to_photo_step(db)
    engine.handle_update(db, document_update())  # como ARCHIVO: conserva el EXIF
    saved = deliver(db, storage, make_image(gps=MEDELLIN_GPS))
    assert texts(saved)[0] == msg.EXP_PHOTO_SAVED_GPS

    summary = texts(finish_without_nit(db, company))[-1]
    assert "<b>Foto:</b> Adjunta ✅ (con GPS)" in summary
    assert "<b>Ubicación:</b> Del GPS de la foto ✅" in summary
    say(db, ("btn", flow.CB_CONFIRM))

    expense = db.scalars(select(Expense)).one()
    document = db.scalars(select(Document)).one()
    assert document.expense_id == expense.id
    assert expense.location_source == LocationSource.EXIF
    assert expense.location_lat == Decimal("6.244167")


def test_ca5_shared_location_wins_over_exif(db, linked, storage, company) -> None:
    to_photo_step(db)
    engine.handle_update(db, document_update())
    deliver(db, storage, make_image(gps=MEDELLIN_GPS))
    finish_without_nit(db, company, location=("loc", 4.6097, -74.0817))

    say(db, ("btn", flow.CB_CONFIRM))

    expense = db.scalars(select(Expense)).one()
    assert expense.location_source == LocationSource.TELEGRAM
    assert expense.location_lat == Decimal("4.609700")


# --- CA-6: foto enviada desde el menú ---


def test_ca6_photo_from_menu_starts_registration(db, linked, storage) -> None:
    actions = engine.handle_update(db, photo_update())

    assert store_actions(actions)
    assert state(db) == flow.HAD_MONEY
    assert msg.EXP_HAD_MONEY not in texts(actions)  # primero se recibe la foto

    saved = deliver(db, storage, JPEG)
    assert texts(saved) == [msg.EXP_PHOTO_SAVED, msg.EXP_HAD_MONEY]  # la pregunta de viáticos se hace igual

    assert texts(say(db, ("btn", flow.CB_NO))) == [msg.EXP_ASK_DESCRIPTION]  # ya no pide la foto


# --- CA-7: foto de otro gasto ---


def test_ca7_photo_of_another_expense_is_not_attached(db, linked, storage, company) -> None:
    to_photo_step(db)
    deliver(db, storage, JPEG)
    finish_without_nit(db, company)
    say(db, ("btn", flow.CB_CONFIRM))  # primer gasto, con esa foto

    to_photo_step(db)
    actions = deliver(db, storage, JPEG)  # la misma foto otra vez

    assert texts(actions) == [msg.EXP_PHOTO_IN_OTHER_EXPENSE, msg.EXP_ASK_PHOTO]
    assert state(db) == flow.PHOTO
    assert "document_id" not in db.get(BotSession, USER).data


def test_second_photo_is_kept_but_not_replacing(db, linked, storage) -> None:
    to_photo_step(db)
    deliver(db, storage, JPEG)

    actions = engine.handle_update(db, photo_update())

    assert texts(actions) == [msg.EXP_ALREADY_HAVE_PHOTO, msg.EXP_ASK_DESCRIPTION]
    assert store_actions(actions) == []


# --- CA-8: RUT ---


def test_ca8_rut_for_new_supplier(db, linked, storage, company) -> None:
    to_photo_step(db)
    say(db, ("btn", flow.CB_NO_PHOTO), "Taxi", "20.000", ("btn", flow.CB_SKIP), ("btn", flow.CB_TODAY),
        "890903938-8", "Bancolombia")
    assert state(db) == flow.RUT

    request = store_actions(engine.handle_update(db, document_update("application/pdf", "rut.pdf")))[0]
    assert request["payload"]["purpose"] == flow.RUT_FILE
    saved = deliver(db, storage, PDF_BYTES, purpose=flow.RUT_FILE)
    assert texts(saved) == [msg.EXP_RUT_SAVED, msg.EXP_ASK_INVOICE]

    say(db, ("btn", flow.CB_SKIP), category_button(db, company, "Transporte"), ("btn", flow.CB_SKIP),
        ("btn", flow.CB_CONFIRM))

    expense = db.scalars(select(Expense)).one()
    rut = db.scalars(select(Document).where(Document.kind == DocumentKind.RUT)).one()
    assert rut.supplier_id == expense.supplier_id
    assert rut.expense_id is None  # el RUT es del proveedor, no de esta factura


# --- CA-9: reemplazar la foto ---


def test_ca9_photo_can_be_replaced_from_edit(db, linked, storage, company) -> None:
    to_photo_step(db)
    deliver(db, storage, JPEG)
    finish_without_nit(db, company)
    first_id = db.get(BotSession, USER).data["document_id"]

    say(db, ("btn", flow.CB_EDIT), ("btn", f"{flow.CB_FIELD}photo"))
    assert state(db) == flow.PHOTO
    engine.handle_update(db, photo_update())
    actions = deliver(db, storage, make_image(color="blue"))

    assert state(db) == flow.CONFIRM
    assert db.get(BotSession, USER).data["document_id"] != first_id
    assert "<b>Foto:</b> Adjunta ✅" in texts(actions)[-1]


def test_ca9_photo_can_be_removed_from_edit(db, linked, storage, company) -> None:
    to_photo_step(db)
    deliver(db, storage, JPEG)
    finish_without_nit(db, company)

    actions = say(db, ("btn", flow.CB_EDIT), ("btn", f"{flow.CB_FIELD}photo"), ("btn", flow.CB_NO_PHOTO))

    assert "<b>Foto:</b> Sin foto" in texts(actions)[-1]
    say(db, ("btn", flow.CB_CONFIRM))
    assert db.scalars(select(Document)).one().expense_id is None  # la foto sigue guardada, sin asociar


# --- Endpoint interno ---


def test_files_endpoint_requires_internal_token(client: TestClient, api_storage) -> None:
    response = client.post("/api/internal/telegram/files", files={"file": ("f.jpg", JPEG, "image/jpeg")},
                           data={"telegram_user_id": str(USER), "purpose": "invoice"})

    assert response.status_code == 401


def test_files_endpoint_stores_and_returns_actions(client: TestClient, api_storage, linked, db) -> None:
    to_photo_step(db)

    response = client.post(
        "/api/internal/telegram/files",
        files={"file": ("f.jpg", JPEG, "image/jpeg")},
        data={"telegram_user_id": str(USER), "purpose": "invoice", "file_unique_id": "ub-1"},
        headers={"X-Internal-Token": get_settings().internal_api_token.get_secret_value()},
    )

    assert response.status_code == 200
    assert [a["payload"]["text"] for a in response.json()["actions"]] == [msg.EXP_PHOTO_SAVED, msg.EXP_ASK_DESCRIPTION]
    assert db.scalars(select(Document)).one().telegram_file_unique_id == "ub-1"


# --- CA-10: el workflow de n8n está bien conectado y solo transporta ---


def test_ca10_n8n_workflow_is_consistent() -> None:
    workflow = json.loads(Path(PROJECT_ROOT, "n8n/workflows/cm-02-entrada-telegram.json").read_text("utf-8"))
    names = {node["name"] for node in workflow["nodes"]}

    for source, outputs in workflow["connections"].items():
        assert source in names
        for branch in outputs["main"]:
            for link in branch:
                assert link["node"] in names, f"{source} apunta a un nodo inexistente: {link['node']}"

    urls = " ".join(str(n["parameters"].get("url", "")) for n in workflow["nodes"])
    assert "/api/internal/telegram/updates" in urls
    assert "/api/internal/telegram/files" in urls
    assert "/getFile" in urls and "api.telegram.org/file/bot" in urls
