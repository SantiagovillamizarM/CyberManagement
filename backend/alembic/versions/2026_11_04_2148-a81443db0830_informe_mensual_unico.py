"""informe mensual unico

Revision ID: a81443db0830
Revises: d47310bd06f1
Create Date: 2026-11-04 21:48:15.324112

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a81443db0830'
down_revision: Union[str, Sequence[str], None] = 'd47310bd06f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Un solo informe mensual automático por empresa y mes (WF7 nunca lo duplica, aunque corra dos veces)."""
    op.execute(
        "CREATE UNIQUE INDEX uq_reports_monthly ON reports (company_id, (filters->>'monthly')) "
        "WHERE filters ? 'monthly'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS uq_reports_monthly")
