"""NIT colombiano y su dígito de verificación (DV).

El DV se calcula con el algoritmo módulo 11 de la DIAN: cada dígito del NIT
(de derecha a izquierda) se multiplica por un peso primo, se suman los
productos y se toma el residuo de dividir por 11.
"""

import re
from dataclasses import dataclass

# Pesos oficiales de la DIAN, aplicados de derecha a izquierda.
_WEIGHTS = (3, 7, 13, 17, 19, 23, 29, 37, 41, 43, 47, 53, 59, 67, 71)


class InvalidNitError(ValueError):
    pass


@dataclass(frozen=True)
class Nit:
    number: str  # solo dígitos, sin DV
    dv: str

    def __str__(self) -> str:
        return f"{self.number}-{self.dv}"


def compute_dv(number: str) -> str:
    if not number.isdigit() or not 1 <= len(number) <= len(_WEIGHTS):
        raise InvalidNitError("El NIT debe tener entre 1 y 15 dígitos")
    total = sum(int(d) * w for d, w in zip(reversed(number), _WEIGHTS))
    remainder = total % 11
    return str(11 - remainder if remainder > 1 else remainder)


def parse_nit(raw: str) -> Nit:
    """Acepta '900.123.456-7', '900123456-7' o '900123456'.

    - Con guion: lo que va después es el DV y se verifica.
    - Sin guion: todo es el número y el DV se calcula. (No se adivina si el
      último dígito es el DV pegado: sería ambiguo.)
    """
    text = raw.strip()
    if "-" in text:
        number_part, dv_part = text.rsplit("-", 1)
        number = re.sub(r"[.\s,]", "", number_part)
        dv = dv_part.strip()
        if not dv.isdigit() or len(dv) != 1:
            raise InvalidNitError("El dígito de verificación debe ser un solo número")
        if compute_dv(number) != dv:
            raise InvalidNitError(f"El dígito de verificación de {number} no es {dv}")
        return Nit(number, dv)

    number = re.sub(r"[.\s,]", "", text)
    return Nit(number, compute_dv(number))
