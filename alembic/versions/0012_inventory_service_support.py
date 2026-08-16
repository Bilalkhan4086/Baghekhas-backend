"""Add persistence support required by the inventory services.

Revision ID: 0012_inventory_support
Revises: 0011_zones_riders
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_inventory_support"
down_revision: str | None = "0011_zones_riders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("purchase_items", sa.Column("manual_overhead", sa.Numeric(14, 2)))
    op.alter_column("inventory_movements", "actor_admin_id", nullable=True)
    op.add_column(
        "inventory_reservations",
        sa.Column("allocation_group_id", postgresql.UUID(as_uuid=True)),
    )
    op.create_table(
        "waste_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        ),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("cost", sa.Numeric(14, 2), nullable=False),
        sa.Column(
            "admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0", name="waste_records_quantity_positive"),
        sa.CheckConstraint("cost >= 0", name="waste_records_cost_nonnegative"),
        sa.CheckConstraint(
            "reason IN ('rotten', 'damaged', 'quality_issue', 'expired', 'lost', 'other')",
            name="waste_records_reason_valid",
        ),
    )
    op.create_index(
        "waste_records_product_created_idx",
        "waste_records",
        ["product_id", "created_at"],
    )
    # M2 retained the pre-batch aggregate as the source of truth. Preserve any
    # existing tracked balance as a zero-cost opening batch before M3 services
    # begin treating batch balances as authoritative.
    op.execute(
        """
        INSERT INTO inventory_batches (
            id, product_id, purchase_item_id, received_quantity,
            remaining_quantity, unit_cost, effective_cost, received_at, created_at
        )
        SELECT
            md5('m3-opening:' || products.id)::uuid,
            products.id,
            NULL,
            products.stock_quantity,
            products.stock_quantity,
            0,
            0,
            CURRENT_TIMESTAMP,
            CURRENT_TIMESTAMP
        FROM products
        WHERE products.inventory_mode = 'tracked'
          AND products.stock_quantity > 0
          AND NOT EXISTS (
              SELECT 1
              FROM inventory_batches
              WHERE inventory_batches.product_id = products.id
          )
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0012_inventory_support is forward-only: removing inventory service data "
        "or restoring non-null movement actors is not lossless."
    )
