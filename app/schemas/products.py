import uuid
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from app.enums import (
    CatalogType,
    InventoryMode,
    InventoryReason,
    PricingType,
    PublicationStatus,
)
from app.schemas.common import APIModel

Quantity = Annotated[Decimal, Field(max_digits=12, decimal_places=3)]
MAX_PRODUCT_IMAGES = 8


def validate_quantity_precision(value: Decimal) -> Decimal:
    try:
        decimal_value = Decimal(value)
        quantized = decimal_value.quantize(Decimal("0.001"))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Quantity must be a decimal number") from error
    if decimal_value != quantized:
        raise ValueError("Quantity supports at most three decimal places")
    return quantized


def validate_product_image_urls(values: list[str]) -> list[str]:
    normalized = [value.strip() for value in values]
    if any(not value for value in normalized):
        raise ValueError("Product image URLs cannot be blank")
    if any(len(value) > 1000 for value in normalized):
        raise ValueError("Product image URLs cannot exceed 1000 characters")
    if len(normalized) > MAX_PRODUCT_IMAGES:
        raise ValueError(f"A product can have at most {MAX_PRODUCT_IMAGES} images")
    if len(set(normalized)) != len(normalized):
        raise ValueError("Product image URLs must be unique")
    return normalized


class ProductBase(APIModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    image_url: str = Field(min_length=1, max_length=1000)
    image_urls: list[str] | None = Field(default=None, min_length=1, max_length=MAX_PRODUCT_IMAGES)
    category: str | None = Field(default=None, max_length=80)
    catalog_type: CatalogType
    unit_label: str = Field(min_length=1, max_length=40)
    tag: str | None = Field(default=None, max_length=80)
    base_price_pkr: int = Field(ge=0, le=100_000_000)
    compare_at_price_pkr: int | None = Field(default=None, ge=0, le=100_000_000)
    pricing_type: PricingType = PricingType.FIXED
    publication_status: PublicationStatus = PublicationStatus.ACTIVE
    is_popular: bool = False
    manual_available: bool = True
    low_stock_threshold: Quantity = Decimal("0")

    @field_validator("name", "description", "image_url", "unit_label")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value cannot be blank")
        return stripped

    @field_validator("category", "tag")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("image_urls")
    @classmethod
    def validate_images(cls, value: list[str] | None) -> list[str] | None:
        return validate_product_image_urls(value) if value is not None else None

    @field_validator("low_stock_threshold")
    @classmethod
    def validate_threshold(cls, value: Decimal) -> Decimal:
        value = validate_quantity_precision(value)
        if value < 0:
            raise ValueError("Low-stock threshold cannot be negative")
        return value

    @model_validator(mode="after")
    def validate_compare_price(self) -> "ProductBase":
        if (
            self.compare_at_price_pkr is not None
            and self.compare_at_price_pkr < self.base_price_pkr
        ):
            raise ValueError("Comparison price cannot be lower than the base price")
        if self.image_urls is None:
            self.image_urls = [self.image_url]
        elif self.image_urls[0] != self.image_url:
            raise ValueError("image_url must match the first image_urls entry")
        return self


class ProductCreate(ProductBase):
    id: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", min_length=1, max_length=120)
    inventory_mode: InventoryMode = InventoryMode.UNTRACKED
    opening_stock: Quantity = Decimal("0")

    @field_validator("opening_stock")
    @classmethod
    def validate_opening_stock(cls, value: Decimal) -> Decimal:
        value = validate_quantity_precision(value)
        if value < 0:
            raise ValueError("Opening stock cannot be negative")
        return value


class ProductUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    image_url: str | None = Field(default=None, min_length=1, max_length=1000)
    image_urls: list[str] | None = Field(default=None, min_length=1, max_length=MAX_PRODUCT_IMAGES)
    category: str | None = Field(default=None, max_length=80)
    catalog_type: CatalogType | None = None
    unit_label: str | None = Field(default=None, min_length=1, max_length=40)
    tag: str | None = Field(default=None, max_length=80)
    base_price_pkr: int | None = Field(default=None, ge=0, le=100_000_000)
    compare_at_price_pkr: int | None = Field(default=None, ge=0, le=100_000_000)
    pricing_type: PricingType | None = None
    publication_status: PublicationStatus | None = None
    is_popular: bool | None = None
    manual_available: bool | None = None
    low_stock_threshold: Quantity | None = None

    @field_validator("name", "description", "image_url", "unit_label")
    @classmethod
    def strip_required_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Value cannot be blank")
        return stripped

    @field_validator("category", "tag")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("image_urls")
    @classmethod
    def validate_images(cls, value: list[str] | None) -> list[str] | None:
        return validate_product_image_urls(value) if value is not None else None

    @field_validator("low_stock_threshold")
    @classmethod
    def validate_threshold(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        value = validate_quantity_precision(value)
        if value < 0:
            raise ValueError("Low-stock threshold cannot be negative")
        return value

    @model_validator(mode="after")
    def require_update(self) -> "ProductUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one product field is required")
        required_fields = {
            "name",
            "description",
            "image_url",
            "image_urls",
            "catalog_type",
            "unit_label",
            "base_price_pkr",
            "pricing_type",
            "publication_status",
            "is_popular",
            "manual_available",
            "low_stock_threshold",
        }
        null_fields = sorted(
            field
            for field in self.model_fields_set & required_fields
            if getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Fields cannot be null: {', '.join(null_fields)}")
        if (
            self.image_urls is not None
            and self.image_url is not None
            and self.image_urls[0] != self.image_url
        ):
            raise ValueError("image_url must match the first image_urls entry")
        return self


class InventoryTrackingUpdate(APIModel):
    mode: InventoryMode
    opening_quantity: Quantity | None = None
    manual_available: bool | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator("opening_quantity")
    @classmethod
    def validate_opening_quantity(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        value = validate_quantity_precision(value)
        if value < 0:
            raise ValueError("Opening quantity cannot be negative")
        return value

    @model_validator(mode="after")
    def validate_mode_fields(self) -> "InventoryTrackingUpdate":
        if self.mode == InventoryMode.TRACKED and self.opening_quantity is None:
            raise ValueError("opening_quantity is required when enabling tracking")
        if self.mode == InventoryMode.UNTRACKED and self.manual_available is None:
            raise ValueError("manual_available is required when disabling tracking")
        return self


class InventoryAdjustmentCreate(APIModel):
    delta: Quantity
    reason: InventoryReason
    note: str | None = Field(default=None, max_length=500)
    reference_order_id: uuid.UUID | None = None

    @field_validator("delta")
    @classmethod
    def validate_delta(cls, value: Decimal) -> Decimal:
        value = validate_quantity_precision(value)
        if value == 0:
            raise ValueError("Inventory adjustment cannot be zero")
        return value


class CatalogProductResponse(APIModel):
    id: str
    name: str
    description: str
    image_url: str
    image_urls: list[str]
    category: str | None
    catalog_type: CatalogType
    unit_label: str
    tag: str | None
    base_price_pkr: int
    compare_at_price_pkr: int | None
    pricing_type: PricingType
    publication_status: PublicationStatus
    is_popular: bool
    is_available: bool
    availability: Literal["in_stock", "available_on_demand", "unavailable"]


class AdminProductResponse(CatalogProductResponse):
    # The admin application still needs the operational availability boolean.
    # The public catalog deliberately receives only is_available/availability.
    available: bool
    inventory_mode: InventoryMode
    manual_available: bool
    stock_quantity: Quantity
    low_stock_threshold: Quantity
    low_stock: bool
    created_at: datetime
    updated_at: datetime


class InventoryMovementResponse(APIModel):
    id: uuid.UUID
    product_id: str
    delta: Quantity
    resulting_quantity: Quantity
    reason: InventoryReason
    note: str | None
    reference_order_id: uuid.UUID | None
    actor_admin_id: uuid.UUID | None
    created_at: datetime
