"""Administrator-only customer profiles and reporting aggregates."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import EmailStr, Field, field_validator, model_validator

from app.enums import OrderStatus
from app.schemas.common import APIModel


class CustomerListItemResponse(APIModel):
    phone: str
    name: str
    email: EmailStr | None
    lifetime_spend_pkr: int
    order_frequency_90d: int
    last_order_at: datetime | None
    created_at: datetime


class CustomerAddressResponse(APIModel):
    id: uuid.UUID
    label: str
    address_text: str
    latitude: Decimal | None
    longitude: Decimal | None
    is_default: bool
    created_at: datetime


class CustomerOrderSummaryResponse(APIModel):
    id: uuid.UUID
    status: OrderStatus
    total_pkr: int
    item_count: int
    created_at: datetime


class FavoriteItemResponse(APIModel):
    product_id: str
    product_name: str
    quantity: Decimal


class CustomerDetailResponse(APIModel):
    phone: str
    name: str
    email: EmailStr | None
    address: str
    lifetime_spend_pkr: int
    order_frequency_90d: int
    favorite_items: list[FavoriteItemResponse]
    addresses: list[CustomerAddressResponse]
    orders: list[CustomerOrderSummaryResponse]
    created_at: datetime
    updated_at: datetime


class CustomerUpdate(APIModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: EmailStr | None = None
    address: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("name", "address")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Value cannot be blank")
        return value

    @model_validator(mode="after")
    def require_update(self) -> CustomerUpdate:
        if not self.model_fields_set:
            raise ValueError("At least one customer field is required")
        null_fields = sorted(
            field
            for field in self.model_fields_set & {"name", "address"}
            if getattr(self, field) is None
        )
        if null_fields:
            raise ValueError(f"Fields cannot be null: {', '.join(null_fields)}")
        return self
