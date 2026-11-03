"""Historial de modificaciones (audit_log).

Cada cambio guarda: qué entidad, qué campo, valor anterior, valor nuevo,
quién lo hizo y cuándo. Las filas solo se insertan, nunca se editan.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from app.models import AdminUser, AuditLog, Employee
from app.models.enums import ActorType


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str | None = None
    ip_address: str | None = None

    @classmethod
    def from_admin(cls, admin: AdminUser, ip_address: str | None = None) -> "Actor":
        return cls(ActorType.ADMIN, str(admin.id), ip_address)

    @classmethod
    def from_employee(cls, employee: Employee) -> "Actor":
        return cls(ActorType.EMPLOYEE, str(employee.id))


SYSTEM = Actor(ActorType.SYSTEM)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)  # Decimal como texto: sin pérdida de precisión
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def log(
    db: Session,
    *,
    company_id: uuid.UUID,
    entity_type: str,
    entity_id: Any,
    action: str,
    actor: Actor,
    field: str | None = None,
    old_value: Any = None,
    new_value: Any = None,
    reason: str | None = None,
) -> None:
    db.add(
        AuditLog(
            company_id=company_id,
            entity_type=entity_type,
            entity_id=str(entity_id),
            action=action,
            field=field,
            old_value=_jsonable(old_value),
            new_value=_jsonable(new_value),
            actor_type=actor.type,
            actor_id=actor.id,
            ip_address=actor.ip_address,
            reason=reason,
        )
    )


def apply_changes(
    db: Session,
    entity: Any,
    changes: dict[str, Any],
    *,
    company_id: uuid.UUID,
    entity_type: str,
    actor: Actor,
) -> dict[str, tuple[Any, Any]]:
    """Asigna los cambios a la entidad y registra una fila por cada campo que realmente cambió.

    Devuelve {campo: (anterior, nuevo)} solo con lo que cambió.
    """
    changed: dict[str, tuple[Any, Any]] = {}
    for field, new_value in changes.items():
        old_value = getattr(entity, field)
        if old_value == new_value:
            continue
        setattr(entity, field, new_value)
        changed[field] = (old_value, new_value)
        log(
            db,
            company_id=company_id,
            entity_type=entity_type,
            entity_id=entity.id,
            action="update",
            actor=actor,
            field=field,
            old_value=old_value,
            new_value=new_value,
        )
    return changed
