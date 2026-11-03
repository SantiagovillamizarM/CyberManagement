"""Categorías de gasto y proveedores (establecimientos)."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Category, Supplier
from app.utils.nit import parse_nit

DEFAULT_CATEGORIES = (
    "Alimentación",
    "Transporte",
    "Hospedaje",
    "Combustible",
    "Peajes",
    "Parqueadero",
    "Papelería",
    "Otros",
)


class CategoryNotFoundError(Exception):
    pass


class DuplicateCategoryError(Exception):
    pass


def ensure_default_categories(db: Session, company_id: uuid.UUID) -> int:
    """Crea las categorías por defecto que falten. Devuelve cuántas creó."""
    existing = set(db.scalars(select(Category.name).where(Category.company_id == company_id)))
    missing = [name for name in DEFAULT_CATEGORIES if name not in existing]
    db.add_all(Category(company_id=company_id, name=name) for name in missing)
    db.flush()
    return len(missing)


def list_categories(db: Session, company_id: uuid.UUID, include_inactive: bool = False) -> list[Category]:
    query = select(Category).where(Category.company_id == company_id)
    if not include_inactive:
        query = query.where(Category.is_active.is_(True))
    return list(db.scalars(query.order_by(Category.name)))


def create_category(db: Session, company_id: uuid.UUID, name: str) -> Category:
    taken = db.scalar(
        select(Category).where(Category.company_id == company_id, Category.name.ilike(name))
    )
    if taken is not None:
        raise DuplicateCategoryError
    category = Category(company_id=company_id, name=name)
    db.add(category)
    db.flush()
    return category


def get_category(db: Session, company_id: uuid.UUID, category_id: uuid.UUID) -> Category:
    category = db.get(Category, category_id)
    if category is None or category.company_id != company_id:
        raise CategoryNotFoundError
    return category


def get_or_create_supplier(
    db: Session, company_id: uuid.UUID, raw_nit: str, name: str | None
) -> Supplier:
    """Busca el proveedor por NIT (validando el DV) o lo crea.

    Lanza InvalidNitError si el NIT o su dígito de verificación no son válidos.
    """
    nit = parse_nit(raw_nit)
    supplier = db.scalar(
        select(Supplier).where(Supplier.company_id == company_id, Supplier.nit == nit.number)
    )
    if supplier is None:
        supplier = Supplier(
            company_id=company_id, nit=nit.number, dv=nit.dv, name=name or f"NIT {nit}"
        )
        db.add(supplier)
        db.flush()
    elif name and supplier.name.startswith("NIT "):
        supplier.name = name  # completa el nombre si antes no se conocía
    return supplier
