"""Add auditable expenses and manual procurement additions.

Revision ID: 0019_expenses_procurement
Revises: 0018_delivery_routes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0019_expenses_procurement"
down_revision: str | None = "0018_delivery_routes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "manual_procurement_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
        sa.Column("note", sa.String(length=500)),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "quantity > 0", name="manual_procurement_items_quantity_positive"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("product_id", name="manual_procurement_items_product_key"),
    )

    op.create_table(
        "expenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("expense_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("amount_pkr", sa.Integer(), nullable=False),
        sa.Column("vendor", sa.String(length=200)),
        sa.Column("notes", sa.String(length=1000)),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True)),
        sa.Column("voided_by_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("amount_pkr > 0", name="expenses_amount_positive"),
        sa.CheckConstraint(
            "category IN ('salaries', 'rent', 'utilities', 'fuel', 'delivery', "
            "'marketing', 'maintenance', 'packaging', 'taxes_and_fees', 'miscellaneous')",
            name="expenses_category_valid",
        ),
        sa.CheckConstraint("status IN ('active', 'voided')", name="expenses_status_valid"),
        sa.CheckConstraint(
            "(status = 'active' AND voided_at IS NULL AND voided_by_id IS NULL) OR "
            "(status = 'voided' AND voided_at IS NOT NULL AND voided_by_id IS NOT NULL)",
            name="expenses_void_state_valid",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["voided_by_id"], ["admin_users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("expenses_date_status_idx", "expenses", ["expense_date", "status"])
    op.create_index("expenses_category_date_idx", "expenses", ["category", "expense_date"])


def downgrade() -> None:
    raise RuntimeError(
        "0019_expenses_procurement is forward-only because expense audit history "
        "must not be discarded."
    )
