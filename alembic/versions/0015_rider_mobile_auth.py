"""Add rider credentials and delivery-start metadata.

Revision ID: 0015_rider_mobile_auth
Revises: 0014_delivery_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_rider_mobile_auth"
down_revision: str | None = "0014_delivery_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing riders are deliberately left without a password until an administrator
    # sets one through the protected rider-management API.
    op.add_column("riders", sa.Column("password_hash", sa.String(length=500)))
    op.add_column(
        "riders",
        sa.Column("auth_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("orders", sa.Column("rider_started_at", sa.DateTime(timezone=True)))


def downgrade() -> None:
    raise RuntimeError(
        "0015_rider_mobile_auth is forward-only because rider access audit timing is retained."
    )
