"""Add secondary product gallery images.

Revision ID: 0021_product_gallery
Revises: 0020_public_order_numbers
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021_product_gallery"
down_revision: str | None = "0020_public_order_numbers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column(
            "gallery_image_urls",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "products_gallery_images_valid",
        "products",
        "jsonb_typeof(gallery_image_urls) = 'array' "
        "AND jsonb_array_length(gallery_image_urls) <= 7",
    )


def downgrade() -> None:
    raise RuntimeError(
        "0021_product_gallery is forward-only because dropping it would discard "
        "secondary product image references."
    )
