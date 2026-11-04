"""Formato de dinero colombiano para mostrar a personas."""

from decimal import Decimal


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
