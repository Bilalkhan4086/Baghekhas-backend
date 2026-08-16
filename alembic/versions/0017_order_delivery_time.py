"""Add a promised delivery time to orders.

Revision ID: 0017_order_delivery_time
Revises: 0016_delivery_integrity
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_order_delivery_time"
down_revision: str | None = "0016_delivery_integrity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("promised_delivery_time", sa.Time()))
    op.execute(
        """
        UPDATE orders
        SET promised_delivery_time = TIME '18:00'
        WHERE promised_delivery_date IS NOT NULL
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "0017_order_delivery_time is forward-only because removing promised delivery "
        "times would discard fulfillment history."
    )
