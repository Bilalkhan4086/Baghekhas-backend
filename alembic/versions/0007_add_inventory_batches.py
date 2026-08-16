"""Add inventory batches with a FIFO-supporting index.

Revision ID: 0007_batches
Revises: 0006_purchases
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_batches"
down_revision: str | None = "0006_purchases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_batches",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id",
            sa.String(120),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purchase_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchase_items.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("received_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("remaining_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("effective_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "received_quantity > 0", name="inventory_batches_received_quantity_positive"
        ),
        sa.CheckConstraint(
            "remaining_quantity >= 0 AND remaining_quantity <= received_quantity",
            name="inventory_batches_remaining_quantity_valid",
        ),
        sa.CheckConstraint("unit_cost >= 0", name="inventory_batches_unit_cost_nonnegative"),
        sa.CheckConstraint(
            "effective_cost >= 0", name="inventory_batches_effective_cost_nonnegative"
        ),
    )
    op.create_index(
        "inventory_batches_product_received_idx",
        "inventory_batches",
        ["product_id", "received_at"],
    )


def downgrade() -> None:
    op.drop_index("inventory_batches_product_received_idx", table_name="inventory_batches")
    op.drop_table("inventory_batches")
