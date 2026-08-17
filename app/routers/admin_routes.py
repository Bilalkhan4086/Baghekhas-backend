"""Administrator visibility and limited control for rider delivery routes."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies import CurrentAdmin, SessionDep
from app.enums import DeliveryRouteStatus
from app.schemas.routes import (
    AdminRouteDetailResponse,
    AdminRoutePageResponse,
    AdminUnroutedReadyResponse,
)
from app.services.routes import AdminRouteService

router = APIRouter(prefix="/admin/delivery-routes", tags=["admin delivery routes"])


@router.get("", response_model=AdminRoutePageResponse)
async def list_routes(
    session: SessionDep,
    _admin: CurrentAdmin,
    delivery_date: date | None = None,
    rider_id: uuid.UUID | None = None,
    route_status: Annotated[
        DeliveryRouteStatus | None, Query(alias="status")
    ] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> AdminRoutePageResponse:
    return await AdminRouteService(session).list_routes(
        delivery_date=delivery_date,
        rider_id=rider_id,
        status=route_status,
        page=page,
        page_size=page_size,
    )


@router.get("/unrouted-ready", response_model=AdminUnroutedReadyResponse)
async def unrouted_ready(
    session: SessionDep,
    _admin: CurrentAdmin,
    delivery_date: date | None = None,
) -> AdminUnroutedReadyResponse:
    return await AdminRouteService(session).unrouted_ready(delivery_date)


@router.get("/{route_id}", response_model=AdminRouteDetailResponse)
async def route_detail(
    route_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> AdminRouteDetailResponse:
    return await AdminRouteService(session).get_route(route_id)


@router.post("/{route_id}/cancel", response_model=AdminRouteDetailResponse)
async def cancel_generated_route(
    route_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> AdminRouteDetailResponse:
    return await AdminRouteService(session).cancel_generated(route_id)
