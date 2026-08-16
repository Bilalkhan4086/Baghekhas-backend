"""Add normalized customer addresses without replacing phone-keyed customers.

Revision ID: 0010_addresses
Revises: 0009_reservations
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_addresses"
down_revision: str | None = "0009_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_addresses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "customer_phone",
            sa.Text(),
            sa.ForeignKey("customers.phone", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("address_text", sa.String(500), nullable=False),
        sa.Column("latitude", sa.Numeric(9, 6)),
        sa.Column("longitude", sa.Numeric(10, 6)),
        sa.Column("is_default", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="customer_addresses_latitude_valid",
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="customer_addresses_longitude_valid",
        ),
    )
    op.create_index(
        "customer_addresses_customer_phone_idx", "customer_addresses", ["customer_phone"]
    )


def downgrade() -> None:
    op.drop_index("customer_addresses_customer_phone_idx", table_name="customer_addresses")
    op.drop_table("customer_addresses")
