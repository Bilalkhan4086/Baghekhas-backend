from __future__ import annotations

import secrets
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, column_property, mapped_column, relationship

from app.enums import InventoryMode, PublicationStatus, StockPolicy

ORDER_NUMBER_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
ORDER_NUMBER_LENGTH = 6


def generate_order_number() -> str:
    """Generate a customer-friendly public reference without ambiguous characters."""
    return "".join(secrets.choice(ORDER_NUMBER_ALPHABET) for _ in range(ORDER_NUMBER_LENGTH))


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AdminUser(TimestampMixin, Base):
    __tablename__ = "admin_users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(500), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )

    purchases: Mapped[list[Purchase]] = relationship(back_populates="created_by", lazy="raise")


class AdminRefreshSession(Base):
    __tablename__ = "admin_refresh_sessions"
    __table_args__ = (Index("admin_refresh_sessions_admin_expires_idx", "admin_id", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    admin: Mapped[AdminUser] = relationship(lazy="raise")


class Product(TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("base_price_pkr >= 0", name="products_base_price_nonnegative"),
        CheckConstraint(
            "compare_at_price_pkr IS NULL OR compare_at_price_pkr >= base_price_pkr",
            name="products_compare_price_valid",
        ),
        CheckConstraint("stock_quantity >= 0", name="products_stock_nonnegative"),
        CheckConstraint("low_stock_threshold >= 0", name="products_threshold_nonnegative"),
        CheckConstraint(
            "catalog_type IN ('product', 'collection')", name="products_catalog_type_valid"
        ),
        CheckConstraint(
            "pricing_type IN ('fixed', 'starting_at')", name="products_pricing_type_valid"
        ),
        CheckConstraint(
            "publication_status IN ('active', 'coming_soon', 'archived')",
            name="products_publication_status_valid",
        ),
        CheckConstraint(
            "inventory_mode IN ('tracked', 'untracked')", name="products_inventory_mode_valid"
        ),
        CheckConstraint(
            "stock_policy IS NULL OR stock_policy IN "
            "('in_stock_only', 'arrange_on_demand', 'preorder')",
            name="products_stock_policy_valid",
        ),
        CheckConstraint(
            "jsonb_typeof(gallery_image_urls) = 'array' "
            "AND jsonb_array_length(gallery_image_urls) <= 7",
            name="products_gallery_images_valid",
        ),
        Index("products_category_idx", "category"),
        Index("products_publication_status_idx", "publication_status"),
    )

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    gallery_image_urls: Mapped[list[str]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb"), nullable=False
    )
    category: Mapped[str | None] = mapped_column(String(80))
    catalog_type: Mapped[str] = mapped_column(String(20), nullable=False)
    unit_label: Mapped[str] = mapped_column(String(40), nullable=False)
    tag: Mapped[str | None] = mapped_column(String(80))
    base_price_pkr: Mapped[int] = mapped_column(Integer, nullable=False)
    compare_at_price_pkr: Mapped[int | None] = mapped_column(Integer)
    pricing_type: Mapped[str] = mapped_column(String(20), nullable=False)
    publication_status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_popular: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    inventory_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    manual_available: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    stock_quantity: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), default=Decimal("0"), server_default="0", nullable=False
    )
    low_stock_threshold: Mapped[Decimal] = mapped_column(
        Numeric(12, 3), default=Decimal("0"), server_default="0", nullable=False
    )
    stock_policy: Mapped[str | None] = mapped_column(String(40))

    inventory_movements: Mapped[list[InventoryMovement]] = relationship(
        back_populates="product", lazy="raise"
    )
    purchase_items: Mapped[list[PurchaseItem]] = relationship(
        back_populates="product", lazy="raise"
    )
    inventory_batches: Mapped[list[InventoryBatch]] = relationship(
        back_populates="product", lazy="raise"
    )
    inventory_reservations: Mapped[list[InventoryReservation]] = relationship(
        back_populates="product", lazy="raise"
    )

    @property
    def effective_stock_policy(self) -> str:
        """Use arrange-on-demand for products created before policy selection existed."""
        return self.stock_policy or StockPolicy.ARRANGE_ON_DEMAND.value

    @property
    def image_urls(self) -> list[str]:
        """Expose the primary image followed by unique secondary gallery images."""
        return list(dict.fromkeys([self.image_url, *(self.gallery_image_urls or [])]))[:8]

    @property
    def available(self) -> bool:
        if self.publication_status != PublicationStatus.ACTIVE.value:
            return False
        if self.inventory_mode == InventoryMode.TRACKED.value:
            return self.stock_quantity > 0 or self.effective_stock_policy in {
                StockPolicy.ARRANGE_ON_DEMAND.value,
                StockPolicy.PREORDER.value,
            }
        return self.manual_available

    @property
    def customer_availability(self) -> str:
        """Customer-safe availability without exposing stock quantities or operations state."""
        if not self.available:
            return "unavailable"
        if (
            self.inventory_mode == InventoryMode.TRACKED.value
            and self.stock_quantity <= 0
            and self.effective_stock_policy
            in {
                StockPolicy.ARRANGE_ON_DEMAND.value,
                StockPolicy.PREORDER.value,
            }
        ):
            return "available_on_demand"
        return "in_stock"

    @property
    def is_available(self) -> bool:
        return self.available

    @property
    def availability(self) -> str:
        return self.customer_availability

    @property
    def low_stock(self) -> bool:
        return (
            self.inventory_mode == InventoryMode.TRACKED.value
            and self.stock_quantity <= self.low_stock_threshold
        )


class Customer(TimestampMixin, Base):
    __tablename__ = "customers"

    phone: Mapped[str] = mapped_column(Text, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))

    orders: Mapped[list[Order]] = relationship(back_populates="customer", lazy="raise")
    addresses: Mapped[list[CustomerAddress]] = relationship(back_populates="customer", lazy="raise")


class Order(TimestampMixin, Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("subtotal_pkr >= 0", name="orders_subtotal_nonnegative"),
        CheckConstraint("delivery_charge_pkr >= 0", name="orders_delivery_charge_nonnegative"),
        CheckConstraint(
            "delivery_charge_pkr <= 350 AND delivery_charge_pkr % 50 = 0",
            name="orders_delivery_charge_tier_valid",
        ),
        CheckConstraint("total_pkr >= 0", name="orders_total_nonnegative"),
        CheckConstraint(
            "total_pkr = subtotal_pkr + delivery_charge_pkr",
            name="orders_total_components_valid",
        ),
        CheckConstraint(
            "(delivery_latitude IS NULL AND delivery_longitude IS NULL AND "
            "delivery_distance_km IS NULL) OR "
            "(delivery_latitude IS NOT NULL AND delivery_longitude IS NOT NULL AND "
            "delivery_distance_km IS NOT NULL)",
            name="orders_delivery_location_complete",
        ),
        CheckConstraint(
            "delivery_latitude IS NULL OR delivery_latitude BETWEEN -90 AND 90",
            name="orders_delivery_latitude_valid",
        ),
        CheckConstraint(
            "delivery_longitude IS NULL OR delivery_longitude BETWEEN -180 AND 180",
            name="orders_delivery_longitude_valid",
        ),
        CheckConstraint(
            "delivery_distance_km IS NULL OR delivery_distance_km >= 0",
            name="orders_delivery_distance_nonnegative",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'packing', 'dispatched', 'delivered', "
            "'not_received', 'completed', 'cancelled', 'refunded')",
            name="orders_status_valid",
        ),
        CheckConstraint(
            "internal_fulfillment_status IS NULL OR internal_fulfillment_status IN "
            "('stock_available', 'procurement_required', 'procurement_in_progress', "
            "'procured', 'ready_for_packing', 'ready_for_dispatch')",
            name="orders_fulfillment_status_valid",
        ),
        CheckConstraint("cogs_pkr IS NULL OR cogs_pkr >= 0", name="orders_cogs_nonnegative"),
        CheckConstraint(
            "refund_amount_pkr IS NULL OR "
            "(refund_amount_pkr > 0 AND refund_amount_pkr <= total_pkr)",
            name="orders_refund_amount_valid",
        ),
        CheckConstraint(
            "(status = 'refunded' AND refund_amount_pkr IS NOT NULL) OR "
            "(status <> 'refunded' AND refund_amount_pkr IS NULL)",
            name="orders_refund_status_valid",
        ),
        CheckConstraint(
            "order_number ~ '^[2-9A-HJ-NP-Z]{6}$'",
            name="orders_order_number_format_valid",
        ),
        Index("orders_order_number_uidx", "order_number", unique=True),
        Index("orders_customer_phone_idx", "customer_phone"),
        Index("orders_created_at_idx", "created_at"),
        Index("orders_status_created_at_idx", "status", "created_at"),
        Index(
            "orders_idempotency_key_uidx",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_number: Mapped[str] = mapped_column(
        String(6), default=generate_order_number, nullable=False
    )
    customer_phone: Mapped[str] = mapped_column(Text, ForeignKey("customers.phone"), nullable=False)
    notes: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(
        String(40), default="pending", server_default="pending", nullable=False
    )
    subtotal_pkr: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_charge_pkr: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_distance_km: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    delivery_latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    delivery_longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    total_pkr: Mapped[int] = mapped_column(Integer, nullable=False)
    refund_amount_pkr: Mapped[int | None] = mapped_column(Integer)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    idempotency_key: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    request_hash: Mapped[str | None] = mapped_column(String(64))
    admin_note: Mapped[str | None] = mapped_column(String(1000))
    customer_name_snapshot: Mapped[str] = mapped_column(String(120), nullable=False)
    delivery_address_snapshot: Mapped[str] = mapped_column(String(500), nullable=False)
    internal_fulfillment_status: Mapped[str | None] = mapped_column(String(40))
    rider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("riders.id", ondelete="RESTRICT")
    )
    cogs_pkr: Mapped[int | None] = mapped_column(Integer)
    delivery_zone_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_zones.id", ondelete="RESTRICT")
    )
    promised_delivery_date: Mapped[date | None] = mapped_column()
    promised_delivery_time: Mapped[time | None] = mapped_column()
    rider_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    customer: Mapped[Customer] = relationship(back_populates="orders", lazy="raise")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="raise"
    )
    status_history: Mapped[list[OrderStatusHistory]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderStatusHistory.created_at",
        lazy="raise",
    )
    inventory_reservations: Mapped[list[InventoryReservation]] = relationship(
        back_populates="order", lazy="raise"
    )
    fulfillment_lines: Mapped[list[OrderFulfillmentLine]] = relationship(
        back_populates="order", cascade="all, delete-orphan", lazy="raise"
    )
    route_stops: Mapped[list[RouteStop]] = relationship(back_populates="order", lazy="raise")

    @property
    def delivery_customer(self) -> dict[str, str]:
        """Return immutable order-time customer identity for response serialization."""
        return {
            "phone": self.customer_phone,
            "name": self.customer_name_snapshot,
            "address": self.delivery_address_snapshot,
        }


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="order_items_order_product_key"),
        CheckConstraint("unit_price_pkr >= 0", name="order_items_unit_price_nonnegative"),
        CheckConstraint("quantity > 0", name="order_items_quantity_positive"),
        CheckConstraint("line_total_pkr >= 0", name="order_items_line_total_nonnegative"),
        Index("order_items_order_id_idx", "order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(String(120), nullable=False)
    product_name: Mapped[str] = mapped_column(String(200), nullable=False)
    unit_label: Mapped[str | None] = column_property(
        select(Product.unit_label)
        .where(Product.id == product_id)
        .correlate_except(Product)
        .scalar_subquery()
    )
    unit_price_pkr: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    line_total_pkr: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items", lazy="raise")


class OrderFulfillmentLine(Base):
    __tablename__ = "order_fulfillment_lines"
    __table_args__ = (
        CheckConstraint("requested_quantity > 0", name="fulfillment_requested_positive"),
        CheckConstraint("reserved_quantity >= 0", name="fulfillment_reserved_nonnegative"),
        CheckConstraint("procurement_quantity >= 0", name="fulfillment_procurement_nonnegative"),
        CheckConstraint("cogs >= 0", name="fulfillment_cogs_nonnegative"),
        CheckConstraint(
            "status IN ('stock_available', 'procurement_required', "
            "'procurement_in_progress', 'procured')",
            name="fulfillment_line_status_valid",
        ),
        UniqueConstraint("order_item_id", name="fulfillment_lines_order_item_key"),
        Index("fulfillment_lines_order_status_idx", "order_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    requested_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    reserved_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    procurement_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    cogs: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0"), nullable=False)

    order: Mapped[Order] = relationship(back_populates="fulfillment_lines", lazy="raise")


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint(
            "reason IN ('opening_balance', 'restock', 'correction', 'damage', 'spoilage', "
            "'order_fulfillment', 'return', 'other')",
            name="inventory_movements_reason_valid",
        ),
        CheckConstraint("resulting_quantity >= 0", name="inventory_result_nonnegative"),
        Index("inventory_movements_product_created_idx", "product_id", "created_at"),
        Index(
            "inventory_movements_reference_type_id_idx",
            "reference_type",
            "reference_id",
        ),
        CheckConstraint(
            "movement_type IS NULL OR movement_type IN "
            "('purchase', 'sale', 'reservation', 'reservation_release', 'waste', 'damage', "
            "'return', 'adjustment_in', 'adjustment_out')",
            name="inventory_movements_movement_type_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    delta: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    resulting_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    reference_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT")
    )
    actor_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_batches.id", ondelete="RESTRICT")
    )
    movement_type: Mapped[str | None] = mapped_column(String(40))
    reference_type: Mapped[str | None] = mapped_column(String(80))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="inventory_movements", lazy="raise")
    actor: Mapped[AdminUser | None] = relationship(lazy="raise")
    batch: Mapped[InventoryBatch | None] = relationship(
        back_populates="inventory_movements", lazy="raise"
    )


class Purchase(TimestampMixin, Base):
    __tablename__ = "purchases"
    __table_args__ = (
        UniqueConstraint("purchase_number", name="purchases_purchase_number_key"),
        CheckConstraint("subtotal >= 0", name="purchases_subtotal_nonnegative"),
        CheckConstraint("additional_cost >= 0", name="purchases_additional_cost_nonnegative"),
        CheckConstraint("total_cost >= 0", name="purchases_total_cost_nonnegative"),
        CheckConstraint(
            "cost_allocation_method IN ('by_weight', 'by_purchase_value', 'manual')",
            name="purchases_cost_allocation_method_valid",
        ),
        CheckConstraint(
            "status IN ('draft', 'received', 'cancelled')", name="purchases_status_valid"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_number: Mapped[str] = mapped_column(String(80), nullable=False)
    supplier: Mapped[str] = mapped_column(String(200), nullable=False)
    purchase_date: Mapped[date] = mapped_column(nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    additional_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_allocation_method: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        "created_by",
        UUID(as_uuid=True),
        ForeignKey("admin_users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    created_by: Mapped[AdminUser] = relationship(back_populates="purchases", lazy="raise")
    items: Mapped[list[PurchaseItem]] = relationship(back_populates="purchase", lazy="raise")
    costs: Mapped[list[PurchaseCost]] = relationship(back_populates="purchase", lazy="raise")


class PurchaseItem(Base):
    __tablename__ = "purchase_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="purchase_items_quantity_positive"),
        CheckConstraint("unit_cost >= 0", name="purchase_items_unit_cost_nonnegative"),
        CheckConstraint("line_cost >= 0", name="purchase_items_line_cost_nonnegative"),
        Index("purchase_items_purchase_id_idx", "purchase_id"),
        Index("purchase_items_product_id_idx", "product_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    manual_overhead: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))

    purchase: Mapped[Purchase] = relationship(back_populates="items", lazy="raise")
    product: Mapped[Product] = relationship(back_populates="purchase_items", lazy="raise")
    inventory_batches: Mapped[list[InventoryBatch]] = relationship(
        back_populates="purchase_item", lazy="raise"
    )


class PurchaseCost(Base):
    __tablename__ = "purchase_costs"
    __table_args__ = (
        CheckConstraint("amount >= 0", name="purchase_costs_amount_nonnegative"),
        CheckConstraint(
            "cost_type IN ('transport', 'loading', 'driver_tip', 'packaging', "
            "'mandi_commission', 'other')",
            name="purchase_costs_cost_type_valid",
        ),
        Index("purchase_costs_purchase_id_idx", "purchase_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    purchase_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchases.id", ondelete="RESTRICT"), nullable=False
    )
    cost_type: Mapped[str] = mapped_column(String(40), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    purchase: Mapped[Purchase] = relationship(back_populates="costs", lazy="raise")


class InventoryBatch(Base):
    __tablename__ = "inventory_batches"
    __table_args__ = (
        CheckConstraint(
            "received_quantity > 0", name="inventory_batches_received_quantity_positive"
        ),
        CheckConstraint(
            "remaining_quantity >= 0 AND remaining_quantity <= received_quantity",
            name="inventory_batches_remaining_quantity_valid",
        ),
        CheckConstraint("unit_cost >= 0", name="inventory_batches_unit_cost_nonnegative"),
        CheckConstraint("effective_cost >= 0", name="inventory_batches_effective_cost_nonnegative"),
        Index("inventory_batches_product_received_idx", "product_id", "received_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_item_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("purchase_items.id", ondelete="RESTRICT")
    )
    received_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    remaining_quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    unit_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    effective_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="inventory_batches", lazy="raise")
    purchase_item: Mapped[PurchaseItem | None] = relationship(
        back_populates="inventory_batches", lazy="raise"
    )
    inventory_movements: Mapped[list[InventoryMovement]] = relationship(
        back_populates="batch", lazy="raise"
    )
    inventory_reservations: Mapped[list[InventoryReservation]] = relationship(
        back_populates="batch", lazy="raise"
    )


class InventoryReservation(Base):
    __tablename__ = "inventory_reservations"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="inventory_reservations_quantity_positive"),
        CheckConstraint(
            "status IN ('active', 'released', 'consumed')",
            name="inventory_reservations_status_valid",
        ),
        Index("inventory_reservations_order_id_idx", "order_id"),
        Index("inventory_reservations_product_status_idx", "product_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    product_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_batches.id", ondelete="RESTRICT")
    )
    allocation_group_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    order: Mapped[Order] = relationship(back_populates="inventory_reservations", lazy="raise")
    product: Mapped[Product] = relationship(back_populates="inventory_reservations", lazy="raise")
    batch: Mapped[InventoryBatch | None] = relationship(
        back_populates="inventory_reservations", lazy="raise"
    )


class WasteRecord(Base):
    __tablename__ = "waste_records"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="waste_records_quantity_positive"),
        CheckConstraint("cost >= 0", name="waste_records_cost_nonnegative"),
        CheckConstraint(
            "reason IN ('rotten', 'damaged', 'quality_issue', 'expired', 'lost', 'other')",
            name="waste_records_reason_valid",
        ),
        Index("waste_records_product_created_idx", "product_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("inventory_batches.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ManualProcurementItem(TimestampMixin, Base):
    __tablename__ = "manual_procurement_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="manual_procurement_items_quantity_positive"),
        UniqueConstraint("product_id", name="manual_procurement_items_product_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    product_id: Mapped[str] = mapped_column(
        String(120), ForeignKey("products.id", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )

    product: Mapped[Product] = relationship(lazy="raise")
    created_by: Mapped[AdminUser] = relationship(lazy="raise")


class Expense(TimestampMixin, Base):
    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount_pkr > 0", name="expenses_amount_positive"),
        CheckConstraint(
            "category IN ('salaries', 'rent', 'utilities', 'fuel', 'delivery', "
            "'marketing', 'maintenance', 'packaging', 'taxes_and_fees', 'miscellaneous')",
            name="expenses_category_valid",
        ),
        CheckConstraint("status IN ('active', 'voided')", name="expenses_status_valid"),
        CheckConstraint(
            "(status = 'active' AND voided_at IS NULL AND voided_by_id IS NULL) OR "
            "(status = 'voided' AND voided_at IS NOT NULL AND voided_by_id IS NOT NULL)",
            name="expenses_void_state_valid",
        ),
        Index("expenses_date_status_idx", "expense_date", "status"),
        Index("expenses_category_date_idx", "category", "expense_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    expense_date: Mapped[date] = mapped_column(nullable=False)
    category: Mapped[str] = mapped_column(String(40), nullable=False)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    amount_pkr: Mapped[int] = mapped_column(Integer, nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(
        String(20), default="active", server_default="active", nullable=False
    )
    created_by_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT"), nullable=False
    )
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="RESTRICT")
    )


class CustomerAddress(Base):
    __tablename__ = "customer_addresses"
    __table_args__ = (
        CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name="customer_addresses_latitude_valid",
        ),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name="customer_addresses_longitude_valid",
        ),
        Index("customer_addresses_customer_phone_idx", "customer_phone"),
        Index(
            "customer_addresses_one_default_uidx",
            "customer_phone",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_phone: Mapped[str] = mapped_column(
        Text, ForeignKey("customers.phone", ondelete="RESTRICT"), nullable=False
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    address_text: Mapped[str] = mapped_column(String(500), nullable=False)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    customer: Mapped[Customer] = relationship(back_populates="addresses", lazy="raise")


class DeliveryZone(Base):
    __tablename__ = "delivery_zones"
    __table_args__ = (UniqueConstraint("name", name="delivery_zones_name_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    boundary: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    rider_zones: Mapped[list[RiderZone]] = relationship(back_populates="zone", lazy="raise")


class Rider(Base):
    __tablename__ = "riders"
    __table_args__ = (UniqueConstraint("phone", name="riders_phone_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(500))
    auth_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rider_zones: Mapped[list[RiderZone]] = relationship(
        back_populates="rider",
        cascade="all, delete-orphan",
        lazy="raise",
    )
    delivery_routes: Mapped[list[DeliveryRoute]] = relationship(
        back_populates="rider", lazy="raise"
    )
    refresh_sessions: Mapped[list[RiderRefreshSession]] = relationship(
        back_populates="rider", cascade="all, delete-orphan", lazy="raise"
    )


class RiderZone(Base):
    __tablename__ = "rider_zones"
    __table_args__ = (
        UniqueConstraint("rider_id", "zone_id", name="rider_zones_rider_zone_key"),
        Index("rider_zones_zone_id_idx", "zone_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("riders.id", ondelete="RESTRICT"), nullable=False
    )
    zone_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_zones.id", ondelete="RESTRICT"), nullable=False
    )

    rider: Mapped[Rider] = relationship(back_populates="rider_zones", lazy="raise")
    zone: Mapped[DeliveryZone] = relationship(back_populates="rider_zones", lazy="raise")


class DeliveryRoute(TimestampMixin, Base):
    __tablename__ = "delivery_routes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('generated', 'in_progress', 'completed', 'cancelled')",
            name="delivery_routes_status_valid",
        ),
        CheckConstraint(
            "start_latitude BETWEEN -90 AND 90",
            name="delivery_routes_start_latitude_valid",
        ),
        CheckConstraint(
            "start_longitude BETWEEN -180 AND 180",
            name="delivery_routes_start_longitude_valid",
        ),
        CheckConstraint(
            "start_source IN ('gps', 'depot')",
            name="delivery_routes_start_source_valid",
        ),
        CheckConstraint(
            "total_distance_meters >= 0",
            name="delivery_routes_distance_nonnegative",
        ),
        CheckConstraint(
            "estimated_duration_seconds >= 0",
            name="delivery_routes_duration_nonnegative",
        ),
        Index(
            "delivery_routes_rider_date_active_uidx",
            "rider_id",
            "delivery_date",
            unique=True,
            postgresql_where=text("status IN ('generated', 'in_progress')"),
        ),
        Index("delivery_routes_date_status_idx", "delivery_date", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("riders.id", ondelete="RESTRICT"), nullable=False
    )
    delivery_date: Mapped[date] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    start_latitude: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    start_longitude: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    start_source: Mapped[str] = mapped_column(String(20), nullable=False)
    total_distance_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rider: Mapped[Rider] = relationship(back_populates="delivery_routes", lazy="raise")
    stops: Mapped[list[RouteStop]] = relationship(
        back_populates="route",
        cascade="all, delete-orphan",
        order_by="RouteStop.sequence",
        lazy="raise",
    )


class RouteStop(TimestampMixin, Base):
    __tablename__ = "route_stops"
    __table_args__ = (
        CheckConstraint("sequence > 0", name="route_stops_sequence_positive"),
        CheckConstraint(
            "status IN ('pending', 'ready', 'in_progress', 'delivered', "
            "'not_received', 'cancelled')",
            name="route_stops_status_valid",
        ),
        CheckConstraint(
            "distance_from_previous_meters >= 0",
            name="route_stops_distance_nonnegative",
        ),
        CheckConstraint(
            "estimated_duration_seconds >= 0",
            name="route_stops_duration_nonnegative",
        ),
        CheckConstraint(
            "not_received_reason IS NULL OR not_received_reason IN "
            "('customer_unavailable', 'customer_refused', 'wrong_address', "
            "'phone_unreachable', 'requested_later', 'other')",
            name="route_stops_not_received_reason_valid",
        ),
        CheckConstraint(
            "not_received_reason <> 'other' OR "
            "(outcome_note IS NOT NULL AND length(trim(outcome_note)) > 0)",
            name="route_stops_other_note_required",
        ),
        UniqueConstraint("route_id", "sequence", name="route_stops_route_sequence_key"),
        UniqueConstraint("route_id", "order_id", name="route_stops_route_order_key"),
        Index(
            "route_stops_route_current_uidx",
            "route_id",
            unique=True,
            postgresql_where=text("status IN ('ready', 'in_progress')"),
        ),
        Index(
            "route_stops_order_unresolved_uidx",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'ready', 'in_progress')"),
        ),
        Index("route_stops_route_status_sequence_idx", "route_id", "status", "sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_routes.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    distance_from_previous_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    estimated_duration_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    not_received_reason: Mapped[str | None] = mapped_column(String(40))
    outcome_note: Mapped[str | None] = mapped_column(String(500))

    route: Mapped[DeliveryRoute] = relationship(back_populates="stops", lazy="raise")
    order: Mapped[Order] = relationship(back_populates="route_stops", lazy="raise")


class RiderRefreshSession(Base):
    __tablename__ = "rider_refresh_sessions"
    __table_args__ = (
        Index("rider_refresh_sessions_rider_expires_idx", "rider_id", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("riders.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    auth_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rider: Mapped[Rider] = relationship(back_populates="refresh_sessions", lazy="raise")


class RiderActionReceipt(Base):
    __tablename__ = "rider_action_receipts"
    __table_args__ = (Index("rider_action_receipts_route_created_idx", "route_id", "created_at"),)

    idempotency_key: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("riders.id", ondelete="RESTRICT"), nullable=False
    )
    route_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("delivery_routes.id", ondelete="RESTRICT"), nullable=False
    )
    stop_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("route_stops.id", ondelete="RESTRICT")
    )
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"
    __table_args__ = (Index("order_status_history_order_created_idx", "order_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(40))
    to_status: Mapped[str] = mapped_column(String(40), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    actor_admin_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("admin_users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship(back_populates="status_history", lazy="raise")
    actor: Mapped[AdminUser | None] = relationship(lazy="raise")
