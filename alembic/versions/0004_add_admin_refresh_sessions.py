"""Add rotating administrator refresh sessions.

Revision ID: 0004_admin_refresh_sessions
Revises: 0003_order_delivery_charges
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_admin_refresh_sessions"
down_revision: str | None = "0003_order_delivery_charges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_refresh_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "admin_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("token_hash", name="admin_refresh_sessions_token_hash_key"),
    )
    op.create_index(
        "admin_refresh_sessions_admin_expires_idx",
        "admin_refresh_sessions",
        ["admin_id", "expires_at"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "This migration is intentionally irreversible because removing refresh sessions "
        "would invalidate active administrator sessions."
    )
