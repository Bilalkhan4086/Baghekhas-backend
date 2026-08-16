"""Add completed/refunded order statuses and refund amount.

Revision ID: 0002_completed_refunded_orders
Revises: 0001_inventory_orders_backend
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_completed_refunded_orders"
down_revision: str | None = "0001_inventory_orders_backend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("refund_amount_pkr", sa.Integer(), nullable=True))
    op.drop_constraint("orders_status_valid", "orders", type_="check")
    op.create_check_constraint(
        "orders_status_valid",
        "orders",
        "status IN ('pending', 'confirmed', 'delivered', 'completed', 'cancelled', "
        "'refunded')",
    )
    op.create_check_constraint(
        "orders_refund_amount_valid",
        "orders",
        "refund_amount_pkr IS NULL OR "
        "(refund_amount_pkr > 0 AND refund_amount_pkr <= total_pkr)",
    )
    op.create_check_constraint(
        "orders_refund_status_valid",
        "orders",
        "(status = 'refunded' AND refund_amount_pkr IS NOT NULL) OR "
        "(status <> 'refunded' AND refund_amount_pkr IS NULL)",
    )


def downgrade() -> None:
    raise RuntimeError(
        "This migration is intentionally irreversible because dropping refund amounts would "
        "destroy financial records. Restore from a tested database backup instead."
    )
