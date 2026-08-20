"""Public-to-rider contracts; deliberately exclude prices and internal fulfilment data."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import Field, field_validator

from app.enums import OrderStatus
from app.schemas.common import APIModel
from app.schemas.orders import OrderNumber


class RiderLoginRequest(APIModel):
    phone: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=500)

    @field_validator("phone")
    @classmethod
    def strip_phone(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Phone is required")
        return value


class RiderIdentityResponse(APIModel):
    id: uuid.UUID
    name: str


class RiderTokenResponse(APIModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int
    rider: RiderIdentityResponse


class RiderRefreshRequest(APIModel):
    refresh_token: str = Field(min_length=43, max_length=500)


class RiderNotReceivedRequest(APIModel):
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class RiderOrderItemResponse(APIModel):
    product_name: str
    quantity: Decimal
    unit_label: str | None = None


class RiderDeliveryListResponse(APIModel):
    id: uuid.UUID
    order_number: OrderNumber
    status: OrderStatus
    customer_name: str
    customer_area: str
    items: list[RiderOrderItemResponse]
    rider_started_at: datetime | None


class RiderDeliveryDetailResponse(APIModel):
    id: uuid.UUID
    order_number: OrderNumber
    status: OrderStatus
    customer_name: str
    customer_phone: str
    delivery_address: str
    items: list[RiderOrderItemResponse]
    rider_started_at: datetime | None
