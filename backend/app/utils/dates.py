"""Fechas en la zona horaria de la empresa (America/Bogota).

En la base de datos todo se guarda en UTC; para reglas de negocio como "la
factura no puede ser de mañana" se usa la fecha local de Colombia.
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def local_today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()


class InvalidDateError(ValueError):
    pass


_DMY = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2}|\d{4})$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def parse_invoice_date(text: str) -> date:
    """Fecha de una factura escrita por una persona: "hoy", "ayer", "03/11/2026", "3-11-26", "2026-11-03".

    Se usa el orden colombiano día/mes/año. No se aceptan fechas futuras.
    """
    value = (text or "").strip().lower()
    today = local_today()
    if value == "hoy":
        return today
    if value == "ayer":
        return today - timedelta(days=1)

    if match := _ISO.match(value):
        year, month, day = (int(g) for g in match.groups())
    elif match := _DMY.match(value):
        day, month, year = (int(g) for g in match.groups())
        if year < 100:
            year += 2000
    else:
        raise InvalidDateError("Escribe la fecha como dd/mm/aaaa, por ejemplo 03/11/2026")

    try:
        result = date(year, month, day)
    except ValueError:
        raise InvalidDateError("Esa fecha no existe")
    if result > today:
        raise InvalidDateError("La fecha de la factura no puede ser futura")
    if result.year < 2000:
        raise InvalidDateError("Revisa el año de la factura")
    return result
