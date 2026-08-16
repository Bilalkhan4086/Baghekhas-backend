"""Add order-engine fulfillment state.

Revision ID: 0013_order_engine
Revises: 0012_inventory_support
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_order_engine"
down_revision: str | None = "0012_inventory_support"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("orders_status_valid", "orders", type_="check")
    op.create_check_constraint(
        "orders_status_valid",
        "orders",
        "status IN ('pending', 'confirmed', 'packing', 'dispatched', 'delivered', "
        "'not_received', 'completed', 'cancelled', 'refunded')",
    )
    op.add_column(
        "orders",
        sa.Column("rider_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "orders_rider_id_fkey",
        "orders",
        "riders",
        ["rider_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("orders", sa.Column("cogs_pkr", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "orders_cogs_nonnegative", "orders", "cogs_pkr IS NULL OR cogs_pkr >= 0"
    )
    op.create_check_constraint(
        "orders_fulfillment_status_valid",
        "orders",
        "internal_fulfillment_status IS NULL OR internal_fulfillment_status IN "
        "('stock_available', 'procurement_required', 'procurement_in_progress', "
        "'procured', 'ready_for_packing', 'ready_for_dispatch')",
    )
    op.create_table(
        "order_fulfillment_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "order_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("order_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("requested_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("reserved_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("procurement_quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("cogs", sa.Numeric(14, 2), server_default="0", nullable=False),
        sa.CheckConstraint("requested_quantity > 0", name="fulfillment_requested_positive"),
        sa.CheckConstraint("reserved_quantity >= 0", name="fulfillment_reserved_nonnegative"),
        sa.CheckConstraint("procurement_quantity >= 0", name="fulfillment_procurement_nonnegative"),
        sa.CheckConstraint("cogs >= 0", name="fulfillment_cogs_nonnegative"),
        sa.CheckConstraint(
            "status IN ('stock_available', 'procurement_required', "
            "'procurement_in_progress', 'procured')",
            name="fulfillment_line_status_valid",
        ),
        sa.UniqueConstraint("order_item_id", name="fulfillment_lines_order_item_key"),
    )
    op.create_index(
        "fulfillment_lines_order_status_idx",
        "order_fulfillment_lines",
        ["order_id", "status"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "0013_order_engine is forward-only because it retains COGS and fulfillment data."
    )
