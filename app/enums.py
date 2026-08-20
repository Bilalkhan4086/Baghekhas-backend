from enum import StrEnum


class CatalogType(StrEnum):
    PRODUCT = "product"
    COLLECTION = "collection"


class PricingType(StrEnum):
    FIXED = "fixed"
    STARTING_AT = "starting_at"


class PublicationStatus(StrEnum):
    ACTIVE = "active"
    COMING_SOON = "coming_soon"
    ARCHIVED = "archived"


class InventoryMode(StrEnum):
    TRACKED = "tracked"
    UNTRACKED = "untracked"


class StockPolicy(StrEnum):
    IN_STOCK_ONLY = "in_stock_only"
    ARRANGE_ON_DEMAND = "arrange_on_demand"
    PREORDER = "preorder"


class PurchaseCostAllocationMethod(StrEnum):
    BY_WEIGHT = "by_weight"
    BY_PURCHASE_VALUE = "by_purchase_value"
    MANUAL = "manual"


class PurchaseStatus(StrEnum):
    DRAFT = "draft"
    RECEIVED = "received"
    CANCELLED = "cancelled"


class PurchaseCostType(StrEnum):
    TRANSPORT = "transport"
    LOADING = "loading"
    DRIVER_TIP = "driver_tip"
    PACKAGING = "packaging"
    MANDI_COMMISSION = "mandi_commission"
    OTHER = "other"


class InventoryMovementType(StrEnum):
    PURCHASE = "purchase"
    SALE = "sale"
    RESERVATION = "reservation"
    RESERVATION_RELEASE = "reservation_release"
    WASTE = "waste"
    DAMAGE = "damage"
    RETURN = "return"
    ADJUSTMENT_IN = "adjustment_in"
    ADJUSTMENT_OUT = "adjustment_out"


class InventoryReservationStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    CONSUMED = "consumed"


class WasteReason(StrEnum):
    ROTTEN = "rotten"
    DAMAGED = "damaged"
    QUALITY_ISSUE = "quality_issue"
    EXPIRED = "expired"
    LOST = "lost"
    OTHER = "other"


class ExpenseCategory(StrEnum):
    SALARIES = "salaries"
    RENT = "rent"
    UTILITIES = "utilities"
    FUEL = "fuel"
    DELIVERY = "delivery"
    MARKETING = "marketing"
    MAINTENANCE = "maintenance"
    PACKAGING = "packaging"
    TAXES_AND_FEES = "taxes_and_fees"
    MISCELLANEOUS = "miscellaneous"


class ExpenseStatus(StrEnum):
    ACTIVE = "active"
    VOIDED = "voided"


class InventoryReason(StrEnum):
    OPENING_BALANCE = "opening_balance"
    RESTOCK = "restock"
    CORRECTION = "correction"
    DAMAGE = "damage"
    SPOILAGE = "spoilage"
    ORDER_FULFILLMENT = "order_fulfillment"
    RETURN = "return"
    OTHER = "other"


class OrderStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    PACKING = "packing"
    DISPATCHED = "dispatched"
    DELIVERED = "delivered"
    NOT_RECEIVED = "not_received"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"


class DeliveryRouteStatus(StrEnum):
    GENERATED = "generated"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class RouteStopStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    NOT_RECEIVED = "not_received"
    CANCELLED = "cancelled"


class NotReceivedReason(StrEnum):
    CUSTOMER_UNAVAILABLE = "customer_unavailable"
    CUSTOMER_REFUSED = "customer_refused"
    WRONG_ADDRESS = "wrong_address"
    PHONE_UNREACHABLE = "phone_unreachable"
    REQUESTED_LATER = "requested_later"
    OTHER = "other"


class CustomerSegment(StrEnum):
    RECENT = "recent"
    INACTIVE = "inactive"
    HIGH_VALUE = "high_value"


ORDER_TRANSITIONS: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.CONFIRMED, OrderStatus.CANCELLED},
    OrderStatus.CONFIRMED: {OrderStatus.PACKING, OrderStatus.CANCELLED},
    OrderStatus.PACKING: {OrderStatus.DISPATCHED, OrderStatus.CANCELLED},
    OrderStatus.DISPATCHED: {OrderStatus.DELIVERED, OrderStatus.NOT_RECEIVED},
    OrderStatus.DELIVERED: {OrderStatus.COMPLETED, OrderStatus.REFUNDED},
    OrderStatus.COMPLETED: {OrderStatus.REFUNDED},
    OrderStatus.CANCELLED: {OrderStatus.REFUNDED},
    OrderStatus.REFUNDED: set(),
}


class FulfillmentStatus(StrEnum):
    STOCK_AVAILABLE = "stock_available"
    PROCUREMENT_REQUIRED = "procurement_required"
    PROCUREMENT_IN_PROGRESS = "procurement_in_progress"
    PROCURED = "procured"
    READY_FOR_PACKING = "ready_for_packing"
    READY_FOR_DISPATCH = "ready_for_dispatch"
