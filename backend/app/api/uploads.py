"""Lectura segura de archivos subidos y traducción de errores de validación a HTTP."""

from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.storage.inspection import FileTooLargeError, InvalidFileError, UnsupportedFileError

T = TypeVar("T")


def read_upload(file: UploadFile) -> bytes:
    """Lee como máximo el límite + 1 byte: un archivo gigante nunca entra completo a memoria."""
    limit = get_settings().max_upload_mb * 1024 * 1024
    return file.file.read(limit + 1)


def handle_file_errors(action: Callable[[], T]) -> T:
    try:
        return action()
    except FileTooLargeError as exc:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, str(exc))
    except UnsupportedFileError as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc))
    except InvalidFileError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
