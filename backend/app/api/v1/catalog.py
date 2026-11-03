"""Categorías de gasto."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, StringConstraints
from sqlalchemy.orm import Session

from app.api.deps import EDITORS, READERS, require_roles
from app.db.session import get_db
from app.models import AdminUser
from app.schemas.expenses import CategoryOut
from app.services import catalog

router = APIRouter(prefix="/categories", tags=["categorías"])


class CategoryCreate(BaseModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=80)]


@router.get("", response_model=list[CategoryOut])
def list_categories(
    admin: AdminUser = Depends(require_roles(*READERS)), db: Session = Depends(get_db)
):
    return catalog.list_categories(db, admin.company_id)


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
def create_category(
    body: CategoryCreate,
    admin: AdminUser = Depends(require_roles(*EDITORS)),
    db: Session = Depends(get_db),
):
    try:
        category = catalog.create_category(db, admin.company_id, body.name)
    except catalog.DuplicateCategoryError:
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe una categoría con ese nombre")
    db.commit()
    return category
