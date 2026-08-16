"""Add inventory, admin authentication, and order management.

This migration deliberately supports both an empty database and the legacy
Next.js checkout schema from Frontend/db/migrations/001_create_orders.sql.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_inventory_orders_backend"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _column_names(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _index_names(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _check_names(table: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(op.get_bind()).get_check_constraints(table)
        if constraint["name"]
    }


def upgrade() -> None:
    tables = _table_names()

    if "customers" not in tables:
        op.create_table(
            "customers",
            sa.Column("phone", sa.Text(), primary_key=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("address", sa.String(500), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )

    if "admin_users" not in tables:
        op.create_table(
            "admin_users",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("password_hash", sa.String(500), nullable=False),
            sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint("email", name="admin_users_email_key"),
        )

    if "products" not in tables:
        op.create_table(
            "products",
            sa.Column("id", sa.String(120), primary_key=True),
            sa.Column("name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("image_url", sa.String(1000), nullable=False),
            sa.Column("category", sa.String(80)),
            sa.Column("catalog_type", sa.String(20), nullable=False),
            sa.Column("unit_label", sa.String(40), nullable=False),
            sa.Column("tag", sa.String(80)),
            sa.Column("base_price_pkr", sa.Integer(), nullable=False),
            sa.Column("compare_at_price_pkr", sa.Integer()),
            sa.Column("pricing_type", sa.String(20), nullable=False),
            sa.Column("publication_status", sa.String(20), nullable=False),
            sa.Column("inventory_mode", sa.String(20), nullable=False),
            sa.Column("manual_available", sa.Boolean(), server_default=sa.true(), nullable=False),
            sa.Column("stock_quantity", sa.Numeric(12, 3), server_default="0", nullable=False),
            sa.Column("low_stock_threshold", sa.Numeric(12, 3), server_default="0", nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint("base_price_pkr >= 0", name="products_base_price_nonnegative"),
            sa.CheckConstraint(
                "compare_at_price_pkr IS NULL OR compare_at_price_pkr >= base_price_pkr",
                name="products_compare_price_valid",
            ),
            sa.CheckConstraint("stock_quantity >= 0", name="products_stock_nonnegative"),
            sa.CheckConstraint("low_stock_threshold >= 0", name="products_threshold_nonnegative"),
            sa.CheckConstraint(
                "catalog_type IN ('product', 'collection')", name="products_catalog_type_valid"
            ),
            sa.CheckConstraint(
                "pricing_type IN ('fixed', 'starting_at')", name="products_pricing_type_valid"
            ),
            sa.CheckConstraint(
                "publication_status IN ('active', 'coming_soon', 'archived')",
                name="products_publication_status_valid",
            ),
            sa.CheckConstraint(
                "inventory_mode IN ('tracked', 'untracked')",
                name="products_inventory_mode_valid",
            ),
        )
        op.create_index("products_category_idx", "products", ["category"])
        op.create_index("products_publication_status_idx", "products", ["publication_status"])

    tables = _table_names()
    if "orders" not in tables:
        op.create_table(
            "orders",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "customer_phone",
                sa.Text(),
                sa.ForeignKey("customers.phone"),
                nullable=False,
            ),
            sa.Column("notes", sa.String(500)),
            sa.Column("status", sa.String(40), server_default="pending", nullable=False),
            sa.Column("total_pkr", sa.Integer(), nullable=False),
            sa.Column("user_agent", sa.String(500)),
            sa.Column("idempotency_key", postgresql.UUID(as_uuid=True)),
            sa.Column("request_hash", sa.String(64)),
            sa.Column("admin_note", sa.String(1000)),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint("total_pkr >= 0", name="orders_total_nonnegative"),
            sa.CheckConstraint(
                "status IN ('pending', 'confirmed', 'delivered', 'cancelled')",
                name="orders_status_valid",
            ),
        )
    else:
        columns = _column_names("orders")
        if "idempotency_key" not in columns:
            op.add_column("orders", sa.Column("idempotency_key", postgresql.UUID(as_uuid=True)))
        if "request_hash" not in columns:
            op.add_column("orders", sa.Column("request_hash", sa.String(64)))
        if "admin_note" not in columns:
            op.add_column("orders", sa.Column("admin_note", sa.String(1000)))
        if "updated_at" not in columns:
            op.add_column(
                "orders",
                sa.Column(
                    "updated_at",
                    sa.DateTime(timezone=True),
                    server_default=sa.func.now(),
                    nullable=False,
                ),
            )
        op.execute("UPDATE orders SET status = 'pending' WHERE status = 'pending_whatsapp'")
        op.alter_column("orders", "status", server_default="pending", existing_type=sa.String(40))
        if "orders_status_valid" not in _check_names("orders"):
            op.create_check_constraint(
                "orders_status_valid",
                "orders",
                "status IN ('pending', 'confirmed', 'delivered', 'cancelled')",
            )

    order_indexes = _index_names("orders")
    if "orders_customer_phone_idx" not in order_indexes:
        op.create_index("orders_customer_phone_idx", "orders", ["customer_phone"])
    if "orders_created_at_idx" not in order_indexes:
        op.create_index("orders_created_at_idx", "orders", [sa.text("created_at DESC")])
    if "orders_status_created_at_idx" not in order_indexes:
        op.create_index("orders_status_created_at_idx", "orders", ["status", "created_at"])
    if "orders_idempotency_key_uidx" not in order_indexes:
        op.create_index(
            "orders_idempotency_key_uidx",
            "orders",
            ["idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )

    tables = _table_names()
    if "order_items" not in tables:
        op.create_table(
            "order_items",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "order_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("product_id", sa.String(120), nullable=False),
            sa.Column("product_name", sa.String(200), nullable=False),
            sa.Column("unit_price_pkr", sa.Integer(), nullable=False),
            sa.Column("quantity", sa.Numeric(12, 3), nullable=False),
            sa.Column("line_total_pkr", sa.Integer(), nullable=False),
            sa.UniqueConstraint("order_id", "product_id", name="order_items_order_product_key"),
            sa.CheckConstraint("unit_price_pkr >= 0", name="order_items_unit_price_nonnegative"),
            sa.CheckConstraint("quantity > 0", name="order_items_quantity_positive"),
            sa.CheckConstraint("line_total_pkr >= 0", name="order_items_line_total_nonnegative"),
        )
    else:
        quantity_type = next(
            column["type"]
            for column in sa.inspect(op.get_bind()).get_columns("order_items")
            if column["name"] == "quantity"
        )
        if not isinstance(quantity_type, sa.Numeric):
            op.alter_column(
                "order_items",
                "quantity",
                existing_type=quantity_type,
                type_=sa.Numeric(12, 3),
                postgresql_using="quantity::numeric(12,3)",
            )
    if "order_items_order_id_idx" not in _index_names("order_items"):
        op.create_index("order_items_order_id_idx", "order_items", ["order_id"])

    tables = _table_names()
    if "inventory_movements" not in tables:
        op.create_table(
            "inventory_movements",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "product_id",
                sa.String(120),
                sa.ForeignKey("products.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("delta", sa.Numeric(12, 3), nullable=False),
            sa.Column("resulting_quantity", sa.Numeric(12, 3), nullable=False),
            sa.Column("reason", sa.String(40), nullable=False),
            sa.Column("note", sa.String(500)),
            sa.Column(
                "reference_order_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="RESTRICT"),
            ),
            sa.Column(
                "actor_admin_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("admin_users.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.CheckConstraint(
                "reason IN ('opening_balance', 'restock', 'correction', 'damage', 'spoilage', "
                "'order_fulfillment', 'return', 'other')",
                name="inventory_movements_reason_valid",
            ),
            sa.CheckConstraint("resulting_quantity >= 0", name="inventory_result_nonnegative"),
        )
        op.create_index(
            "inventory_movements_product_created_idx",
            "inventory_movements",
            ["product_id", "created_at"],
        )

    if "order_status_history" not in tables:
        op.create_table(
            "order_status_history",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "order_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("orders.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("from_status", sa.String(40)),
            sa.Column("to_status", sa.String(40), nullable=False),
            sa.Column("note", sa.String(500)),
            sa.Column(
                "actor_admin_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("admin_users.id", ondelete="SET NULL"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "order_status_history_order_created_idx",
            "order_status_history",
            ["order_id", "created_at"],
        )
        op.execute(
            """
            INSERT INTO order_status_history (
              id, order_id, from_status, to_status, note, created_at
            )
            SELECT gen_random_uuid(), o.id, NULL, o.status, 'Migrated existing order', o.created_at
            FROM orders o
            WHERE NOT EXISTS (
              SELECT 1 FROM order_status_history h WHERE h.order_id = o.id
            )
            """
        )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION add_initial_order_status_history()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
          INSERT INTO order_status_history (
            id, order_id, from_status, to_status, note, created_at
          ) VALUES (
            gen_random_uuid(), NEW.id, NULL, NEW.status, 'Order created', NEW.created_at
          );
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER IF EXISTS orders_initial_status_history ON orders")
    op.execute(
        """
        CREATE TRIGGER orders_initial_status_history
        AFTER INSERT ON orders
        FOR EACH ROW EXECUTE FUNCTION add_initial_order_status_history()
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "This migration is intentionally irreversible because downgrading decimal quantities "
        "and inventory history could destroy production data. Restore from a Neon branch instead."
    )
