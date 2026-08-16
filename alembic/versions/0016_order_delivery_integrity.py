"""Snapshot delivery identity and enforce one default customer address.

Revision ID: 0016_delivery_integrity
Revises: 0015_rider_mobile_auth
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_delivery_integrity"
down_revision: str | None = "0015_rider_mobile_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("customer_name_snapshot", sa.String(120)))
    op.add_column("orders", sa.Column("delivery_address_snapshot", sa.String(500)))
    op.execute(
        """
        UPDATE orders
        SET customer_name_snapshot = customers.name,
            delivery_address_snapshot = customers.address
        FROM customers
        WHERE customers.phone = orders.customer_phone
          AND (orders.customer_name_snapshot IS NULL
               OR orders.delivery_address_snapshot IS NULL)
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fill_order_customer_snapshots()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.customer_name_snapshot IS NULL OR NEW.delivery_address_snapshot IS NULL THEN
            SELECT name, address
            INTO NEW.customer_name_snapshot, NEW.delivery_address_snapshot
            FROM customers
            WHERE phone = NEW.customer_phone;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS orders_fill_customer_snapshots ON orders")
    op.execute(
        """
        CREATE TRIGGER orders_fill_customer_snapshots
        BEFORE INSERT ON orders
        FOR EACH ROW EXECUTE FUNCTION fill_order_customer_snapshots()
        """
    )
    op.alter_column("orders", "customer_name_snapshot", nullable=False)
    op.alter_column("orders", "delivery_address_snapshot", nullable=False)

    op.execute(
        """
        WITH ranked_defaults AS (
          SELECT id,
                 row_number() OVER (
                   PARTITION BY customer_phone
                   ORDER BY created_at DESC, id DESC
                 ) AS position
          FROM customer_addresses
          WHERE is_default
        )
        UPDATE customer_addresses
        SET is_default = false
        FROM ranked_defaults
        WHERE customer_addresses.id = ranked_defaults.id
          AND ranked_defaults.position > 1
        """
    )
    op.create_index(
        "customer_addresses_one_default_uidx",
        "customer_addresses",
        ["customer_phone"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    raise RuntimeError(
        "0016_delivery_integrity is forward-only because removing immutable delivery "
        "snapshots would discard historical fulfillment data."
    )
