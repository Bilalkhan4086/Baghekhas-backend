"""Add delivery-zone assignment and promised delivery date to orders.

Revision ID: 0014_delivery_engine
Revises: 0013_order_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_delivery_engine"
down_revision: str | None = "0013_order_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("delivery_zone_id", postgresql.UUID(as_uuid=True)))
    op.create_foreign_key(
        "orders_delivery_zone_id_fkey",
        "orders",
        "delivery_zones",
        ["delivery_zone_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.add_column("orders", sa.Column("promised_delivery_date", sa.Date()))


def downgrade() -> None:
    raise RuntimeError(
        "0014_delivery_engine is forward-only because delivery promises are historical records."
    )
