"""Tipos compartidos entre esquemas."""

import uuid
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, Field

# Dinero en COP: nunca negativo, máximo 2 decimales y 12 dígitos enteros.
# En las respuestas JSON viaja como texto ("150000.00") para no perder precisión.
Money = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
SignedMoney = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]


class EmployeeRef(BaseModel):
    id: uuid.UUID
    first_name: str
    last_name: str
