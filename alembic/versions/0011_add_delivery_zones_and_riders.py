"""Add delivery zones, riders, and their assignments.

Revision ID: 0011_zones_riders
Revises: 0010_addresses
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_zones_riders"
down_revision: str | None = "0010_addresses"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "delivery_zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("boundary", postgresql.JSONB()),
        sa.UniqueConstraint("name", name="delivery_zones_name_key"),
    )
    op.create_table(
        "riders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("phone", sa.String(30), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("phone", name="riders_phone_key"),
    )
    op.create_table(
        "rider_zones",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "rider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "zone_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("delivery_zones.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint("rider_id", "zone_id", name="rider_zones_rider_zone_key"),
    )
    op.create_index("rider_zones_zone_id_idx", "rider_zones", ["zone_id"])


def downgrade() -> None:
    op.drop_index("rider_zones_zone_id_idx", table_name="rider_zones")
    op.drop_table("rider_zones")
    op.drop_table("riders")
    op.drop_table("delivery_zones")
