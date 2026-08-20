"""Add six-character public order numbers.

Revision ID: 0020_public_order_numbers
Revises: 0019_expenses_procurement
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_public_order_numbers"
down_revision: str | None = "0019_expenses_procurement"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("order_number", sa.String(length=6), nullable=True))
    op.execute(
        """
        DO $$
        DECLARE
            alphabet CONSTANT text := '23456789ABCDEFGHJKLMNPQRSTUVWXYZ';
            namespace_size CONSTANT bigint := 1073741824;
            existing_orders bigint;
        BEGIN
            SELECT count(*) INTO existing_orders FROM orders;
            IF existing_orders >= namespace_size THEN
                RAISE EXCEPTION 'Six-character order-number namespace is exhausted';
            END IF;

            WITH ranked AS (
                SELECT
                    id,
                    row_number() OVER (ORDER BY created_at, id) - 1 AS number_value
                FROM orders
            ),
            encoded AS (
                SELECT
                    id,
                    substr(alphabet, ((number_value / 33554432) % 32)::integer + 1, 1) ||
                    substr(alphabet, ((number_value / 1048576) % 32)::integer + 1, 1) ||
                    substr(alphabet, ((number_value / 32768) % 32)::integer + 1, 1) ||
                    substr(alphabet, ((number_value / 1024) % 32)::integer + 1, 1) ||
                    substr(alphabet, ((number_value / 32) % 32)::integer + 1, 1) ||
                    substr(alphabet, (number_value % 32)::integer + 1, 1) AS order_number
                FROM ranked
            )
            UPDATE orders
            SET order_number = encoded.order_number
            FROM encoded
            WHERE orders.id = encoded.id;
        END $$
        """
    )
    op.alter_column("orders", "order_number", nullable=False)
    op.create_check_constraint(
        "orders_order_number_format_valid",
        "orders",
        "order_number ~ '^[2-9A-HJ-NP-Z]{6}$'",
    )
    op.create_index(
        "orders_order_number_uidx",
        "orders",
        ["order_number"],
        unique=True,
    )


def downgrade() -> None:
    raise RuntimeError(
        "0020_public_order_numbers is forward-only because removing customer-facing "
        "order references would break tracking and support workflows."
    )
