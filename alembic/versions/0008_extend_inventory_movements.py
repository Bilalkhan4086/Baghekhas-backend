"""Add batch and polymorphic references to the existing movement log.

Revision ID: 0008_movement_refs
Revises: 0007_batches
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_movement_refs"
down_revision: str | None = "0007_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "inventory_movements",
        sa.Column(
            "batch_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inventory_batches.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column("inventory_movements", sa.Column("movement_type", sa.String(40)))
    op.add_column("inventory_movements", sa.Column("reference_type", sa.String(80)))
    op.add_column(
        "inventory_movements", sa.Column("reference_id", postgresql.UUID(as_uuid=True))
    )
    op.create_check_constraint(
        "inventory_movements_movement_type_valid",
        "inventory_movements",
        "movement_type IS NULL OR movement_type IN "
        "('purchase', 'sale', 'reservation', 'reservation_release', 'waste', 'damage', "
        "'return', 'adjustment_in', 'adjustment_out')",
    )
    op.create_index(
        "inventory_movements_reference_type_id_idx",
        "inventory_movements",
        ["reference_type", "reference_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "inventory_movements_reference_type_id_idx", table_name="inventory_movements"
    )
    op.drop_constraint(
        "inventory_movements_movement_type_valid", "inventory_movements", type_="check"
    )
    op.drop_column("inventory_movements", "reference_id")
    op.drop_column("inventory_movements", "reference_type")
    op.drop_column("inventory_movements", "movement_type")
    op.drop_column("inventory_movements", "batch_id")
