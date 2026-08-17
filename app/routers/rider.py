"""Minimal rider-only delivery API, scoped to the authenticated rider."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Response, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.dependencies import CurrentRider, SessionDep, SettingsDep
from app.enums import OrderStatus
from app.exceptions import DomainError, not_found
from app.models import Order, Rider
from app.schemas.rider import (
    RiderDeliveryDetailResponse,
    RiderDeliveryListResponse,
    RiderIdentityResponse,
    RiderLoginRequest,
    RiderNotReceivedRequest,
    RiderOrderItemResponse,
    RiderRefreshRequest,
    RiderTokenResponse,
)
from app.security import verify_password
from app.services.delivery import KARACHI
from app.services.order_transitions import OrderTransitionService
from app.services.rider_auth import (
    issue_rider_token_pair,
    revoke_rider_refresh_token,
    rotate_rider_refresh_token,
)

router = APIRouter(prefix="/rider", tags=["rider"])


def _items(order: Order) -> list[RiderOrderItemResponse]:
    return [
        RiderOrderItemResponse(product_name=item.product_name, quantity=item.quantity)
        for item in order.items
    ]


def _customer_area(address: str) -> str:
    """Return a minimal list label; the full address remains detail-only."""
    parts = [part.strip() for part in address.split(",") if part.strip()]
    if len(parts) >= 3:
        return parts[-2]
    return "Open delivery for address"


def _list_response(order: Order) -> RiderDeliveryListResponse:
    return RiderDeliveryListResponse(
        id=order.id,
        status=OrderStatus(order.status),
        customer_name=order.customer_name_snapshot,
        customer_area=_customer_area(order.delivery_address_snapshot),
        items=_items(order),
        rider_started_at=order.rider_started_at,
    )


def _detail_response(order: Order) -> RiderDeliveryDetailResponse:
    return RiderDeliveryDetailResponse(
        id=order.id,
        status=OrderStatus(order.status),
        customer_name=order.customer_name_snapshot,
        customer_phone=order.customer.phone,
        delivery_address=order.delivery_address_snapshot,
        items=_items(order),
        rider_started_at=order.rider_started_at,
    )


async def _clear_read_transaction(session: SessionDep) -> None:
    if session.in_transaction():
        await session.rollback()


async def _assigned_order(session: SessionDep, order_id: uuid.UUID, rider_id: uuid.UUID) -> Order:
    order = await session.scalar(
        select(Order)
        .where(Order.id == order_id)
        .options(selectinload(Order.customer), selectinload(Order.items))
    )
    if order is None:
        raise not_found("Order")
    if order.rider_id != rider_id:
        raise DomainError(403, "order_not_assigned", "This order is not assigned to you")
    return order


@router.post("/auth/login", response_model=RiderTokenResponse)
async def login(
    payload: RiderLoginRequest, session: SessionDep, settings: SettingsDep
) -> RiderTokenResponse:
    rider = await session.scalar(select(Rider).where(Rider.phone == payload.phone))
    if rider is None or rider.password_hash is None or not verify_password(
        payload.password, rider.password_hash
    ):
        raise DomainError(401, "invalid_credentials", "Phone or password is incorrect")
    if not rider.is_active:
        raise DomainError(403, "rider_inactive", "Rider account is inactive")
    return await issue_rider_token_pair(session, rider, settings)


@router.post("/auth/refresh", response_model=RiderTokenResponse)
async def refresh(
    payload: RiderRefreshRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> RiderTokenResponse:
    return await rotate_rider_refresh_token(session, payload.refresh_token, settings)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RiderRefreshRequest, session: SessionDep) -> Response:
    await revoke_rider_refresh_token(session, payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=RiderIdentityResponse)
async def me(rider: CurrentRider) -> RiderIdentityResponse:
    return RiderIdentityResponse(id=rider.id, name=rider.name)


@router.get(
    "/me/deliveries/today",
    response_model=list[RiderDeliveryListResponse],
    deprecated=True,
)
async def today_deliveries(
    session: SessionDep, rider: CurrentRider
) -> list[RiderDeliveryListResponse]:
    today = datetime.now(KARACHI).date()
    orders = list(
        (
            await session.scalars(
                select(Order)
                .where(
                    Order.rider_id == rider.id,
                    Order.promised_delivery_date == today,
                    Order.status == OrderStatus.DISPATCHED.value,
                )
                .options(selectinload(Order.customer), selectinload(Order.items))
                .order_by(Order.rider_started_at.is_(None).desc(), Order.created_at, Order.id)
            )
        ).all()
    )
    return [_list_response(order) for order in orders]


@router.get(
    "/orders/{order_id}", response_model=RiderDeliveryDetailResponse, deprecated=True
)
async def delivery_detail(
    order_id: uuid.UUID, session: SessionDep, rider: CurrentRider
) -> RiderDeliveryDetailResponse:
    return _detail_response(await _assigned_order(session, order_id, rider.id))


@router.post(
    "/orders/{order_id}/start", response_model=RiderDeliveryDetailResponse, deprecated=True
)
async def start_delivery(
    order_id: uuid.UUID, session: SessionDep, rider: CurrentRider
) -> RiderDeliveryDetailResponse:
    rider_id = rider.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).start_rider_delivery(order_id, rider_id)
    return _detail_response(await _assigned_order(session, order_id, rider_id))


@router.post(
    "/orders/{order_id}/delivered",
    response_model=RiderDeliveryDetailResponse,
    deprecated=True,
)
async def mark_delivered(
    order_id: uuid.UUID, session: SessionDep, rider: CurrentRider
) -> RiderDeliveryDetailResponse:
    rider_id = rider.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).deliver_order(
        order_id, None, assigned_rider_id=rider_id
    )
    return _detail_response(await _assigned_order(session, order_id, rider_id))


@router.post(
    "/orders/{order_id}/not-received",
    response_model=RiderDeliveryDetailResponse,
    deprecated=True,
)
async def mark_not_received(
    order_id: uuid.UUID,
    payload: RiderNotReceivedRequest,
    session: SessionDep,
    rider: CurrentRider,
) -> RiderDeliveryDetailResponse:
    rider_id = rider.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).mark_not_received(
        order_id,
        None,
        payload.note or "Rider marked customer as not received",
        assigned_rider_id=rider_id,
    )
    return _detail_response(await _assigned_order(session, order_id, rider_id))
