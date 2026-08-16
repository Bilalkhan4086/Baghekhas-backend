"""Add required-for-new-orders delivery location and tiered charges.

Revision ID: 0003_order_delivery_charges
Revises: 0002_completed_refunded_orders
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_order_delivery_charges"
down_revision: str | None = "0002_completed_refunded_orders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("subtotal_pkr", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("delivery_charge_pkr", sa.Integer(), nullable=True))
    op.add_column("orders", sa.Column("delivery_distance_km", sa.Numeric(8, 3)))
    op.add_column("orders", sa.Column("delivery_latitude", sa.Numeric(9, 6)))
    op.add_column("orders", sa.Column("delivery_longitude", sa.Numeric(10, 6)))
    op.execute(
        "UPDATE orders SET subtotal_pkr = total_pkr, delivery_charge_pkr = 0 "
        "WHERE subtotal_pkr IS NULL OR delivery_charge_pkr IS NULL"
    )
    op.alter_column("orders", "subtotal_pkr", nullable=False)
    op.alter_column("orders", "delivery_charge_pkr", nullable=False)
    op.create_check_constraint(
        "orders_subtotal_nonnegative", "orders", "subtotal_pkr >= 0"
    )
    op.create_check_constraint(
        "orders_delivery_charge_nonnegative", "orders", "delivery_charge_pkr >= 0"
    )
    op.create_check_constraint(
        "orders_delivery_charge_tier_valid",
        "orders",
        "delivery_charge_pkr <= 350 AND delivery_charge_pkr % 50 = 0",
    )
    op.create_check_constraint(
        "orders_total_components_valid",
        "orders",
        "total_pkr = subtotal_pkr + delivery_charge_pkr",
    )
    op.create_check_constraint(
        "orders_delivery_location_complete",
        "orders",
        "(delivery_latitude IS NULL AND delivery_longitude IS NULL AND "
        "delivery_distance_km IS NULL) OR "
        "(delivery_latitude IS NOT NULL AND delivery_longitude IS NOT NULL AND "
        "delivery_distance_km IS NOT NULL)",
    )
    op.create_check_constraint(
        "orders_delivery_latitude_valid",
        "orders",
        "delivery_latitude IS NULL OR delivery_latitude BETWEEN -90 AND 90",
    )
    op.create_check_constraint(
        "orders_delivery_longitude_valid",
        "orders",
        "delivery_longitude IS NULL OR delivery_longitude BETWEEN -180 AND 180",
    )
    op.create_check_constraint(
        "orders_delivery_distance_nonnegative",
        "orders",
        "delivery_distance_km IS NULL OR delivery_distance_km >= 0",
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fill_legacy_order_pricing_components()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          IF NEW.delivery_charge_pkr IS NULL THEN
            NEW.delivery_charge_pkr := 0;
          END IF;
          IF NEW.subtotal_pkr IS NULL THEN
            NEW.subtotal_pkr := NEW.total_pkr - NEW.delivery_charge_pkr;
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS orders_fill_legacy_pricing ON orders")
    op.execute(
        """
        CREATE TRIGGER orders_fill_legacy_pricing
        BEFORE INSERT ON orders
        FOR EACH ROW EXECUTE FUNCTION fill_legacy_order_pricing_components()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "This migration is intentionally irreversible because dropping delivery charges and "
        "customer coordinates would destroy order records. Restore from a tested backup instead."
    )
