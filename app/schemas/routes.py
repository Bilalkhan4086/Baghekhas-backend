"""Privacy-scoped contracts for persistent rider delivery routes."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, model_validator

from app.enums import DeliveryRouteStatus, NotReceivedReason, OrderStatus, RouteStopStatus
from app.schemas.common import APIModel
from app.schemas.orders import OrderNumber
from app.schemas.rider import RiderOrderItemResponse


class RouteGenerateRequest(APIModel):
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(
        default=None, ge=-180, le=180, max_digits=10, decimal_places=6
    )
    use_depot_fallback: bool = False

    @model_validator(mode="after")
    def require_location_or_fallback(self) -> RouteGenerateRequest:
        has_latitude = self.latitude is not None
        has_longitude = self.longitude is not None
        if has_latitude != has_longitude:
            raise ValueError("Latitude and longitude must be supplied together")
        if self.use_depot_fallback == has_latitude:
            raise ValueError("Provide GPS coordinates or confirm the depot fallback")
        return self


class RouteProgressResponse(APIModel):
    total: int
    delivered: int
    not_received: int
    completed: int
    remaining: int
    current_sequence: int | None


class RouteStopPreviewResponse(APIModel):
    id: uuid.UUID
    order_id: uuid.UUID
    order_number: OrderNumber
    sequence: int
    status: RouteStopStatus
    customer_name: str
    customer_area: str
    distance_from_previous_meters: int
    estimated_duration_seconds: int
    completed_at: datetime | None
    not_received_reason: NotReceivedReason | None
    outcome_note: str | None


class CurrentRouteStopResponse(RouteStopPreviewResponse):
    customer_phone: str
    delivery_address: str
    latitude: Decimal
    longitude: Decimal
    items: list[RiderOrderItemResponse]
    started_at: datetime | None


class AdminRouteStopResponse(RouteStopPreviewResponse):
    latitude: Decimal
    longitude: Decimal


class RiderRouteResponse(APIModel):
    id: uuid.UUID
    delivery_date: date
    status: DeliveryRouteStatus
    start_source: str
    total_distance_meters: int
    estimated_duration_seconds: int
    started_at: datetime | None
    completed_at: datetime | None
    progress: RouteProgressResponse
    preview_stops: list[RouteStopPreviewResponse]
    current_stop: CurrentRouteStopResponse | None
    updated_at: datetime


class TodaySummaryResponse(APIModel):
    total: int
    delivered: int
    not_received: int
    remaining: int
    routeable: int
    blocked: int
    active_route: RiderRouteResponse | None


class RouteNotReceivedRequest(APIModel):
    reason: NotReceivedReason
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_other_note(self) -> RouteNotReceivedRequest:
        self.note = self.note.strip() or None if self.note is not None else None
        if self.reason == NotReceivedReason.OTHER and self.note is None:
            raise ValueError("A note is required when the reason is other")
        return self


class RiderHistoryItemResponse(APIModel):
    id: uuid.UUID
    order_number: OrderNumber
    status: OrderStatus
    customer_name: str
    customer_area: str
    items: list[RiderOrderItemResponse]
    outcome_at: datetime | None
    not_received_reason: NotReceivedReason | None
    outcome_note: str | None


class RiderHistoryPageResponse(APIModel):
    items: list[RiderHistoryItemResponse]
    page: int
    page_size: int
    total: int


class AdminRouteSummaryResponse(APIModel):
    id: uuid.UUID
    rider_id: uuid.UUID
    rider_name: str
    delivery_date: date
    status: DeliveryRouteStatus
    total_distance_meters: int
    estimated_duration_seconds: int
    progress: RouteProgressResponse
    current_order_id: uuid.UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AdminRoutePageResponse(APIModel):
    items: list[AdminRouteSummaryResponse]
    page: int
    page_size: int
    total: int


class AdminRouteDetailResponse(AdminRouteSummaryResponse):
    start_latitude: Decimal
    start_longitude: Decimal
    start_source: str
    stops: list[AdminRouteStopResponse]


class OrderRouteStateResponse(APIModel):
    route_id: uuid.UUID | None
    route_status: DeliveryRouteStatus | None
    stop_status: RouteStopStatus | None
    locked: bool


class AdminUnroutedReadyResponse(APIModel):
    total: int
    order_ids: list[uuid.UUID]
