"""Add the administrator-managed product popularity flag.

Revision ID: 0022_product_popularity
Revises: 0021_product_gallery
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022_product_popularity"
down_revision: str | None = "0021_product_gallery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("is_popular", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    raise RuntimeError(
        "0022_product_popularity is forward-only because dropping it would discard "
        "administrator-managed storefront merchandising choices."
    )
