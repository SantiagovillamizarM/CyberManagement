"""Dinero colombiano: mostrarlo a personas y entender cómo lo escriben."""

import re
from decimal import Decimal, InvalidOperation

MAX_AMOUNT = Decimal("999999999999.99")  # límite de NUMERIC(14,2)


class InvalidAmountError(ValueError):
    pass


def format_cop(value: Decimal | None) -> str:
    """Decimal("1234567.5") -> "$1.234.567,50"; Decimal("45000") -> "$45.000"."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    value = abs(value).quantize(Decimal("0.01"))
    integer, cents = divmod(value, 1)
    text = f"{int(integer):,}".replace(",", ".")
    if cents:
        text += "," + f"{cents:.2f}"[2:]
    return f"{sign}${text}"


def _valid_groups(groups: list[str]) -> bool:
    """Separadores de miles: el primer grupo de 1 a 3 dígitos y los demás de 3 exactos."""
    return 1 <= len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:])


def parse_cop(text: str) -> Decimal:
    """Entiende cómo escribe un colombiano un valor en pesos.

    "45000", "45.000", "$ 45.000", "1.234.567,50", "45000,5", "45,000.50" -> Decimal.
    Regla para un solo separador: si le siguen exactamente 3 dígitos es de miles
    ("45.000" = cuarenta y cinco mil); si le siguen 1 o 2 es decimal ("45,5").
    """
    cleaned = re.sub(r"(?i)\s|\$|cop|pesos", "", text or "")
    if not cleaned or not re.fullmatch(r"[0-9.,]+", cleaned) or not cleaned[0].isdigit():
        raise InvalidAmountError("No entendí el valor")

    has_dot, has_comma = "." in cleaned, "," in cleaned
    if has_dot and has_comma:
        decimal_sep = "." if cleaned.rfind(".") > cleaned.rfind(",") else ","
        thousands_sep = "," if decimal_sep == "." else "."
        integer_part, _, decimals = cleaned.rpartition(decimal_sep)
        groups = integer_part.split(thousands_sep)
        if decimal_sep in integer_part or not _valid_groups(groups) or not 1 <= len(decimals) <= 2:
            raise InvalidAmountError("No entendí el valor")
        normalized = "".join(groups) + "." + decimals
    elif has_dot or has_comma:
        sep = "." if has_dot else ","
        parts = cleaned.split(sep)
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            normalized = parts[0] + "." + parts[1]  # decimal
        elif _valid_groups(parts):
            normalized = "".join(parts)  # miles
        else:
            raise InvalidAmountError("No entendí el valor")
    else:
        normalized = cleaned

    try:
        value = Decimal(normalized).quantize(Decimal("0.01"))
    except InvalidOperation:
        raise InvalidAmountError("No entendí el valor")
    if value <= 0:
        raise InvalidAmountError("El valor debe ser mayor que cero")
    if value > MAX_AMOUNT:
        raise InvalidAmountError("El valor es demasiado grande")
    return value
