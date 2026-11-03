"""Fechas en la zona horaria de la empresa (America/Bogota).

En la base de datos todo se guarda en UTC; para reglas de negocio como "la
factura no puede ser de mañana" se usa la fecha local de Colombia.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.core.config import get_settings


def local_today() -> date:
    return datetime.now(ZoneInfo(get_settings().timezone)).date()
