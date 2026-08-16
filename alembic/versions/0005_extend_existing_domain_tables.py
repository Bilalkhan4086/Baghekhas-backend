"""Add nullable domain columns to existing tables.

Revision ID: 0005_domain_ext
Revises: 0004_admin_refresh_sessions
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_domain_ext"
down_revision: str | None = "0004_admin_refresh_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("products", sa.Column("stock_policy", sa.String(40), nullable=True))
    op.create_check_constraint(
        "products_stock_policy_valid",
        "products",
        "stock_policy IS NULL OR stock_policy IN "
        "('in_stock_only', 'arrange_on_demand', 'preorder')",
    )
    op.add_column("customers", sa.Column("email", sa.String(320), nullable=True))
    op.add_column(
        "orders", sa.Column("internal_fulfillment_status", sa.String(40), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("orders", "internal_fulfillment_status")
    op.drop_column("customers", "email")
    op.drop_constraint("products_stock_policy_valid", "products", type_="check")
    op.drop_column("products", "stock_policy")
