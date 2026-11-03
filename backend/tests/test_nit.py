"""CA-1: NIT colombiano con dígito de verificación (algoritmo módulo 11 de la DIAN)."""

import pytest

from app.utils.nit import InvalidNitError, compute_dv, parse_nit


@pytest.mark.parametrize(
    ("number", "dv"),
    [
        ("800197268", "4"),  # DIAN
        ("890903938", "8"),  # Bancolombia
    ],
)
def test_known_real_nits(number: str, dv: str) -> None:
    assert compute_dv(number) == dv


@pytest.mark.parametrize("raw", ["800.197.268-4", "800197268-4", " 800 197 268 - 4 ", "800,197,268-4"])
def test_accepts_common_formats(raw: str) -> None:
    nit = parse_nit(raw)

    assert (nit.number, nit.dv) == ("800197268", "4")
    assert str(nit) == "800197268-4"


def test_without_dv_it_is_computed() -> None:
    assert parse_nit("890903938").dv == "8"


def test_wrong_dv_is_rejected() -> None:
    with pytest.raises(InvalidNitError, match="no es 5"):
        parse_nit("800197268-5")


@pytest.mark.parametrize("raw", ["", "ABC123", "1234567890123456", "800197268-", "800197268-45"])
def test_invalid_input_is_rejected(raw: str) -> None:
    with pytest.raises(InvalidNitError):
        parse_nit(raw)
