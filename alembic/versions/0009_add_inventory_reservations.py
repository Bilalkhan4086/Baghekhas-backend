"""Add batch-aware inventory reservations.

Revision ID: 0009_reservations
Revises: 0008_movement_refs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0009_reservations"
down_revision: str | None = "0008_movement_refs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "inventory_reservations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.String(120),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("quantity > 0", name="inventory_reservations_quantity_positive"),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name="inventory_reservations_status_valid",
        ),
    )
    op.create_index(
        "inventory_reservations_order_id_idx", "inventory_reservations", ["order_id"]
    )
    op.create_index(
        "inventory_reservations_product_status_idx",
        "inventory_reservations",
        ["product_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "inventory_reservations_product_status_idx", table_name="inventory_reservations"
    )
    op.drop_index("inventory_reservations_order_id_idx", table_name="inventory_reservations")
    op.drop_table("inventory_reservations")
