"""Add purchase, purchase item, and purchase cost tables.

Revision ID: 0006_purchases
Revises: 0005_domain_ext
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_purchases"
down_revision: str | None = "0005_domain_ext"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "purchases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("purchase_number", sa.String(80), nullable=False),
        sa.Column("supplier", sa.String(200), nullable=False),
        sa.Column("purchase_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.Column("subtotal", sa.Numeric(14, 2), nullable=False),
        sa.Column("additional_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("total_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("cost_allocation_method", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column(
            "created_by",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("purchase_number", name="purchases_purchase_number_key"),
        sa.CheckConstraint("subtotal >= 0", name="purchases_subtotal_nonnegative"),
        sa.CheckConstraint(
            "additional_cost >= 0", name="purchases_additional_cost_nonnegative"
        ),
        sa.CheckConstraint("total_cost >= 0", name="purchases_total_cost_nonnegative"),
        sa.CheckConstraint(
            "cost_allocation_method IN ('by_weight', 'by_purchase_value', 'manual')",
            name="purchases_cost_allocation_method_valid",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'received', 'cancelled')", name="purchases_status_valid"
        ),
    )
    op.create_table(
        "purchase_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "purchase_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            sa.String(120),
            sa.ForeignKey("products.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("unit_cost", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_cost", sa.Numeric(14, 2), nullable=False),
        sa.CheckConstraint("quantity > 0", name="purchase_items_quantity_positive"),
        sa.CheckConstraint("unit_cost >= 0", name="purchase_items_unit_cost_nonnegative"),
        sa.CheckConstraint("line_cost >= 0", name="purchase_items_line_cost_nonnegative"),
    )
    op.create_index("purchase_items_purchase_id_idx", "purchase_items", ["purchase_id"])
    op.create_index("purchase_items_product_id_idx", "purchase_items", ["product_id"])
    op.create_table(
        "purchase_costs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "purchase_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("purchases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("cost_type", sa.String(40), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint("amount >= 0", name="purchase_costs_amount_nonnegative"),
        sa.CheckConstraint(
            "cost_type IN ('transport', 'loading', 'driver_tip', 'packaging', "
            "'mandi_commission', 'other')",
            name="purchase_costs_cost_type_valid",
        ),
    )
    op.create_index("purchase_costs_purchase_id_idx", "purchase_costs", ["purchase_id"])


def downgrade() -> None:
    op.drop_index("purchase_costs_purchase_id_idx", table_name="purchase_costs")
    op.drop_table("purchase_costs")
    op.drop_index("purchase_items_product_id_idx", table_name="purchase_items")
    op.drop_index("purchase_items_purchase_id_idx", table_name="purchase_items")
    op.drop_table("purchase_items")
    op.drop_table("purchases")
