import uuid
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, field_validator, model_validator

from app.enums import FulfillmentStatus, OrderStatus
from app.schemas.common import APIModel
from app.schemas.products import validate_quantity_precision

OrderQuantity = Annotated[Decimal, Field(gt=0, le=99, max_digits=12, decimal_places=3)]
FulfillmentQuantity = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=3)]
DeliveryDistance = Annotated[Decimal, Field(ge=0, max_digits=8, decimal_places=3)]
OrderNumber = Annotated[str, Field(pattern=r"^[2-9A-HJ-NP-Z]{6}$")]


class DeliveryLocationInput(APIModel):
    latitude: Decimal = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: Decimal = Field(ge=-180, le=180, allow_inf_nan=False)

    @field_validator("latitude", "longitude")
    @classmethod
    def normalize_coordinate(cls, value: Decimal) -> Decimal:
        return value.quantize(Decimal("0.000001"), ROUND_HALF_UP)


class CustomerInput(APIModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=1, max_length=30)
    address: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("name", "phone", "address")
    @classmethod
    def strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value cannot be blank")
        return stripped

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class OrderItemInput(APIModel):
    product_id: str = Field(
        validation_alias=AliasChoices("product_id", "id"), min_length=1, max_length=120
    )
    quantity: OrderQuantity

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: Decimal) -> Decimal:
        return validate_quantity_precision(value)


class OrderCreate(APIModel):
    customer: CustomerInput
    delivery_location: DeliveryLocationInput
    items: list[OrderItemInput] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def reject_duplicate_products(self) -> "OrderCreate":
        product_ids = [item.product_id for item in self.items]
        if len(product_ids) != len(set(product_ids)):
            raise ValueError("Cart contains duplicate products")
        return self


class AdminOrderCreate(OrderCreate):
    delivery_charge_pkr: int | None = Field(default=None, ge=0, le=100_000_000)


class CustomerResponse(APIModel):
    phone: str
    name: str
    address: str


class OrderItemResponse(APIModel):
    id: uuid.UUID
    product_id: str
    product_name: str
    unit_label: str | None = None
    unit_price_pkr: int
    quantity: OrderQuantity
    line_total_pkr: int


class OrderStatusHistoryResponse(APIModel):
    id: uuid.UUID
    from_status: OrderStatus | None
    to_status: OrderStatus
    note: str | None
    actor_admin_id: uuid.UUID | None
    created_at: datetime


class OrderFulfillmentLineResponse(APIModel):
    order_item_id: uuid.UUID
    requested_quantity: OrderQuantity
    reserved_quantity: FulfillmentQuantity
    procurement_quantity: FulfillmentQuantity
    status: FulfillmentStatus


class OrderActionsResponse(APIModel):
    actions: list[str]


class CancellationRequest(APIModel):
    reason: str | None = Field(default=None, max_length=500)


class NotReceivedRequest(APIModel):
    notes: str = Field(min_length=1, max_length=500)

    @field_validator("notes")
    @classmethod
    def strip_notes(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Resolution notes are required")
        return value


class DispatchRequest(APIModel):
    rider_id: uuid.UUID | None = None


class RiderReassignmentRequest(APIModel):
    rider_id: uuid.UUID


class OrderSummaryResponse(APIModel):
    id: uuid.UUID
    order_number: OrderNumber
    status: OrderStatus
    subtotal_pkr: int
    delivery_charge_pkr: int
    delivery_distance_km: DeliveryDistance | None
    total_pkr: int
    refund_amount_pkr: int | None
    internal_fulfillment_status: FulfillmentStatus | None
    rider_id: uuid.UUID | None
    promised_delivery_date: date | None
    promised_delivery_time: time | None
    customer_phone: str
    customer_name: str
    item_count: int
    created_at: datetime
    updated_at: datetime


class OrderResponse(APIModel):
    id: uuid.UUID
    order_number: OrderNumber
    status: OrderStatus
    subtotal_pkr: int
    delivery_charge_pkr: int
    delivery_distance_km: DeliveryDistance | None
    delivery_latitude: Decimal | None
    delivery_longitude: Decimal | None
    total_pkr: int
    refund_amount_pkr: int | None
    internal_fulfillment_status: FulfillmentStatus | None
    rider_id: uuid.UUID | None
    promised_delivery_date: date | None
    promised_delivery_time: time | None
    notes: str | None
    admin_note: str | None
    customer: CustomerResponse = Field(
        validation_alias=AliasChoices("delivery_customer", "customer")
    )
    items: list[OrderItemResponse]
    status_history: list[OrderStatusHistoryResponse]
    fulfillment_lines: list[OrderFulfillmentLineResponse]
    created_at: datetime
    updated_at: datetime


class PublicOrderResponse(APIModel):
    id: uuid.UUID
    order_number: OrderNumber
    status: OrderStatus
    subtotal_pkr: int
    delivery_charge_pkr: int
    delivery_distance_km: DeliveryDistance | None
    delivery_latitude: Decimal | None
    delivery_longitude: Decimal | None
    total_pkr: int
    refund_amount_pkr: int | None
    notes: str | None
    customer: CustomerResponse = Field(
        validation_alias=AliasChoices("delivery_customer", "customer")
    )
    items: list[OrderItemResponse]
    status_history: list[OrderStatusHistoryResponse]
    created_at: datetime
    updated_at: datetime


class OrderTrackingRequest(APIModel):
    order_number: OrderNumber
    phone: str = Field(min_length=1, max_length=30)

    @field_validator("order_number", mode="before")
    @classmethod
    def normalize_order_number(cls, value: object) -> object:
        return value.strip().upper() if isinstance(value, str) else value


class PublicOrderTrackingItem(APIModel):
    product_name: str
    quantity: OrderQuantity
    unit_label: str | None = None


class PublicOrderTrackingEvent(APIModel):
    status: OrderStatus
    created_at: datetime


class PublicOrderTrackingResponse(APIModel):
    order_number: OrderNumber
    status: OrderStatus
    promised_delivery_date: date | None
    promised_delivery_time: time | None
    items: list[PublicOrderTrackingItem]
    status_history: list[PublicOrderTrackingEvent]
    created_at: datetime
    updated_at: datetime


class OrderAdminUpdate(APIModel):
    status: Literal[OrderStatus.COMPLETED, OrderStatus.REFUNDED] | None = None
    refund_amount_pkr: int | None = Field(default=None, gt=0)
    admin_note: str | None = Field(default=None, max_length=1000)
    status_note: str | None = Field(default=None, max_length=500)
    promised_delivery_date: date | None = None
    promised_delivery_time: time | None = None

    @field_validator("promised_delivery_time")
    @classmethod
    def normalize_promised_delivery_time(cls, value: time | None) -> time | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            raise ValueError("promised_delivery_time must be a Karachi local time")
        return value.replace(second=0, microsecond=0)

    @model_validator(mode="after")
    def require_update(self) -> "OrderAdminUpdate":
        allowed_updates = {
            "status",
            "admin_note",
            "promised_delivery_date",
            "promised_delivery_time",
        }
        if not self.model_fields_set.intersection(allowed_updates):
            raise ValueError("A status, admin_note, or delivery schedule update is required")
        if (
            "promised_delivery_date" in self.model_fields_set
            and self.promised_delivery_date is None
        ):
            raise ValueError("promised_delivery_date cannot be null")
        if (
            "promised_delivery_time" in self.model_fields_set
            and self.promised_delivery_time is None
        ):
            raise ValueError("promised_delivery_time cannot be null")
        if self.status_note is not None and self.status is None:
            raise ValueError("status_note requires a status change")
        if self.status == OrderStatus.REFUNDED and self.refund_amount_pkr is None:
            raise ValueError("refund_amount_pkr is required when refunding an order")
        if "refund_amount_pkr" in self.model_fields_set and self.status != OrderStatus.REFUNDED:
            raise ValueError("refund_amount_pkr is only allowed when refunding an order")
        return self


class DeliveryChargeBandResponse(APIModel):
    over_distance_km: Decimal | None
    up_to_distance_km: Decimal | None
    charge_pkr: int


class DeliverySettingsResponse(APIModel):
    origin_latitude: Decimal
    origin_longitude: Decimal
    free_radius_km: Decimal
    charge_bands: list[DeliveryChargeBandResponse]
    maximum_charge_pkr: int


class DeliveryQuoteResponse(APIModel):
    distance_km: DeliveryDistance
    delivery_charge_pkr: int
    promised_delivery_date: date | None = None
    promised_delivery_time: time | None = None
