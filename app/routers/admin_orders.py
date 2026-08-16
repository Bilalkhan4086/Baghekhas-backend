import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, Query, Request, Response, status

from app.dependencies import CurrentAdmin, SessionDep, SettingsDep
from app.enums import FulfillmentStatus, OrderStatus
from app.exceptions import DomainError
from app.models import Order
from app.schemas.common import Page
from app.schemas.orders import (
    CancellationRequest,
    DeliveryLocationInput,
    DeliveryQuoteResponse,
    DeliverySettingsResponse,
    DispatchRequest,
    NotReceivedRequest,
    OrderActionsResponse,
    OrderAdminUpdate,
    OrderCreate,
    OrderResponse,
    OrderStatusHistoryResponse,
    OrderSummaryResponse,
    RiderReassignmentRequest,
)
from app.services.delivery import DeliveryScheduleService, RiderAssignmentService
from app.services.order_transitions import OrderTransitionService
from app.services.orders import (
    DELIVERY_ORIGIN_LATITUDE,
    DELIVERY_ORIGIN_LONGITUDE,
    DELIVERY_TIER_CHARGE_PKR,
    DELIVERY_TIER_SIZE_KM,
    FREE_DELIVERY_RADIUS_KM,
    MAXIMUM_DELIVERY_CHARGE_PKR,
    calculate_delivery_quote,
    create_order,
    get_order_for_admin,
    list_admin_orders,
    update_order,
)

router = APIRouter(prefix="/admin/orders", tags=["admin orders"])


@router.get("", response_model=Page[OrderSummaryResponse])
async def list_orders(
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: OrderStatus | None = None,
    internal_fulfillment_status: FulfillmentStatus | None = None,
    q: str | None = Query(default=None, max_length=200),
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> Page[OrderSummaryResponse]:
    return await list_admin_orders(
        session,
        page=page,
        page_size=page_size,
        status=status.value if status else None,
        internal_fulfillment_status=internal_fulfillment_status,
        query=q,
        date_from=date_from,
        date_to=date_to,
    )


@router.post("", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def add_order(
    payload: OrderCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    _admin: CurrentAdmin,
    settings: SettingsDep,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> OrderResponse:
    order, created = await create_order(
        session,
        payload,
        idempotency_key=idempotency_key,
        user_agent=request.headers.get("user-agent"),
        delivery_cutoff_hour=settings.delivery_cutoff_hour,
        delivery_default_time=settings.delivery_default_time,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return OrderResponse.model_validate(order)


@router.get("/delivery-settings", response_model=DeliverySettingsResponse)
async def get_delivery_settings(_admin: CurrentAdmin) -> DeliverySettingsResponse:
    return DeliverySettingsResponse(
        origin_latitude=DELIVERY_ORIGIN_LATITUDE,
        origin_longitude=DELIVERY_ORIGIN_LONGITUDE,
        free_radius_km=FREE_DELIVERY_RADIUS_KM,
        tier_size_km=DELIVERY_TIER_SIZE_KM,
        tier_charge_pkr=DELIVERY_TIER_CHARGE_PKR,
        maximum_charge_pkr=MAXIMUM_DELIVERY_CHARGE_PKR,
    )


@router.post("/delivery-quote", response_model=DeliveryQuoteResponse)
async def get_delivery_quote(
    payload: DeliveryLocationInput, _admin: CurrentAdmin, settings: SettingsDep
) -> DeliveryQuoteResponse:
    distance_km, delivery_charge_pkr = calculate_delivery_quote(
        payload.latitude,
        payload.longitude,
    )
    delivery_schedule = DeliveryScheduleService(
        settings.delivery_cutoff_hour, settings.delivery_default_time
    )
    return DeliveryQuoteResponse(
        distance_km=distance_km,
        delivery_charge_pkr=delivery_charge_pkr,
        promised_delivery_date=delivery_schedule.calculate_delivery_date(datetime.now(UTC)),
        promised_delivery_time=delivery_schedule.calculate_delivery_time(),
    )


@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(order_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin) -> Order:
    return await get_order_for_admin(session, order_id)


@router.patch("/{order_id}", response_model=OrderResponse)
async def edit_order(
    order_id: uuid.UUID,
    payload: OrderAdminUpdate,
    session: SessionDep,
    admin: CurrentAdmin,
) -> Order:
    return await update_order(session, order_id, payload, admin)


@router.get("/{order_id}/status-history", response_model=list[OrderStatusHistoryResponse])
async def get_status_history(
    order_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> list[OrderStatusHistoryResponse]:
    order = await get_order_for_admin(session, order_id)
    return [OrderStatusHistoryResponse.model_validate(item) for item in order.status_history]


async def _clear_read_transaction(session: SessionDep) -> None:
    if session.in_transaction():
        await session.rollback()


async def _updated_order(session: SessionDep, order_id: uuid.UUID) -> Order:
    return await get_order_for_admin(session, order_id)


@router.get("/{order_id}/available-actions", response_model=OrderActionsResponse)
async def get_available_actions(
    order_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> OrderActionsResponse:
    order = await get_order_for_admin(session, order_id)
    return OrderActionsResponse(actions=OrderTransitionService.available_actions(order))


@router.post("/{order_id}/confirm", response_model=OrderResponse)
async def confirm_order(order_id: uuid.UUID, session: SessionDep, admin: CurrentAdmin) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).confirm_order(order_id, admin_id)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/start-packing", response_model=OrderResponse)
async def start_packing(order_id: uuid.UUID, session: SessionDep, admin: CurrentAdmin) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).start_packing(order_id, admin_id)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/dispatch", response_model=OrderResponse)
async def dispatch_order(
    order_id: uuid.UUID, payload: DispatchRequest, session: SessionDep, admin: CurrentAdmin
) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).dispatch_order(order_id, payload.rider_id, admin_id)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/deliver", response_model=OrderResponse)
async def deliver_order(order_id: uuid.UUID, session: SessionDep, admin: CurrentAdmin) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).deliver_order(order_id, admin_id)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/not-received", response_model=OrderResponse)
async def mark_not_received(
    order_id: uuid.UUID, payload: NotReceivedRequest, session: SessionDep, admin: CurrentAdmin
) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).mark_not_received(order_id, admin_id, payload.notes)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/cancel", response_model=OrderResponse)
async def cancel_order(
    order_id: uuid.UUID, payload: CancellationRequest, session: SessionDep, admin: CurrentAdmin
) -> Order:
    if not payload.reason or not payload.reason.strip():
        raise DomainError(400, "cancellation_reason_required", "A cancellation reason is required")
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).cancel_order(order_id, admin_id, payload.reason)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/start-procurement", response_model=OrderResponse)
async def start_procurement(order_id: uuid.UUID, session: SessionDep, admin: CurrentAdmin) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).mark_procurement_in_progress(order_id, admin_id)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/mark-procured", response_model=OrderResponse)
async def mark_procured(order_id: uuid.UUID, session: SessionDep, admin: CurrentAdmin) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await OrderTransitionService(session).mark_procured(order_id, admin_id)
    return await _updated_order(session, order_id)


@router.post("/{order_id}/reassign-rider", response_model=OrderResponse)
async def reassign_rider(
    order_id: uuid.UUID,
    payload: RiderReassignmentRequest,
    session: SessionDep,
    admin: CurrentAdmin,
) -> Order:
    admin_id = admin.id
    await _clear_read_transaction(session)
    await RiderAssignmentService(session).reassign_rider(order_id, payload.rider_id, admin_id)
    return await _updated_order(session, order_id)
