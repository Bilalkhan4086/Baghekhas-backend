"""Authenticated rider route, stop, summary, and history endpoints."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Header, Query, status

from app.dependencies import CurrentRider, SessionDep, SettingsDep
from app.enums import OrderStatus
from app.schemas.routes import (
    RiderHistoryItemResponse,
    RiderHistoryPageResponse,
    RiderRouteResponse,
    RouteGenerateRequest,
    RouteNotReceivedRequest,
    TodaySummaryResponse,
)
from app.services.routes import DeliveryRouteService

router = APIRouter(prefix="/rider", tags=["rider routes"])


@router.get("/me/summary", response_model=TodaySummaryResponse)
async def today_summary(
    session: SessionDep, settings: SettingsDep, rider: CurrentRider
) -> TodaySummaryResponse:
    return await DeliveryRouteService(session, settings).today_summary(rider.id)


@router.get("/me/deliveries/history", response_model=RiderHistoryPageResponse)
async def delivery_history(
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
    date_from: date | None = None,
    date_to: date | None = None,
    delivery_status: Annotated[
        OrderStatus | None, Query(alias="status")
    ] = None,
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> RiderHistoryPageResponse:
    return await DeliveryRouteService(session, settings).history(
        rider_id=rider.id,
        date_from=date_from,
        date_to=date_to,
        status=delivery_status,
        query=q,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/me/deliveries/history/{order_id}", response_model=RiderHistoryItemResponse
)
async def delivery_history_detail(
    order_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
) -> RiderHistoryItemResponse:
    return await DeliveryRouteService(session, settings).history_detail(order_id, rider.id)


@router.post(
    "/routes/generate",
    response_model=RiderRouteResponse,
    status_code=status.HTTP_201_CREATED,
)
async def generate_route(
    payload: RouteGenerateRequest,
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> RiderRouteResponse:
    return await DeliveryRouteService(session, settings).generate_route(
        rider_id=rider.id,
        latitude=payload.latitude,
        longitude=payload.longitude,
        use_depot_fallback=payload.use_depot_fallback,
        idempotency_key=idempotency_key,
    )


@router.get("/routes/active", response_model=RiderRouteResponse | None)
async def active_route(
    session: SessionDep, settings: SettingsDep, rider: CurrentRider
) -> RiderRouteResponse | None:
    return await DeliveryRouteService(session, settings).get_active_route(rider.id)


@router.get("/routes/{route_id}", response_model=RiderRouteResponse)
async def route_detail(
    route_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
) -> RiderRouteResponse:
    return await DeliveryRouteService(session, settings).get_rider_route(route_id, rider.id)


@router.post("/routes/{route_id}/start", response_model=RiderRouteResponse)
async def start_route(
    route_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> RiderRouteResponse:
    return await DeliveryRouteService(session, settings).start_route(
        route_id, rider.id, idempotency_key
    )


@router.post(
    "/routes/{route_id}/stops/{stop_id}/start", response_model=RiderRouteResponse
)
async def start_stop(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> RiderRouteResponse:
    return await DeliveryRouteService(session, settings).start_stop(
        route_id, stop_id, rider.id, idempotency_key
    )


@router.post(
    "/routes/{route_id}/stops/{stop_id}/delivered", response_model=RiderRouteResponse
)
async def mark_stop_delivered(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> RiderRouteResponse:
    return await DeliveryRouteService(session, settings).complete_stop(
        route_id=route_id,
        stop_id=stop_id,
        rider_id=rider.id,
        idempotency_key=idempotency_key,
        delivered=True,
    )


@router.post(
    "/routes/{route_id}/stops/{stop_id}/not-received",
    response_model=RiderRouteResponse,
)
async def mark_stop_not_received(
    route_id: uuid.UUID,
    stop_id: uuid.UUID,
    payload: RouteNotReceivedRequest,
    session: SessionDep,
    settings: SettingsDep,
    rider: CurrentRider,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> RiderRouteResponse:
    return await DeliveryRouteService(session, settings).complete_stop(
        route_id=route_id,
        stop_id=stop_id,
        rider_id=rider.id,
        idempotency_key=idempotency_key,
        delivered=False,
        reason=payload.reason,
        note=payload.note,
    )
