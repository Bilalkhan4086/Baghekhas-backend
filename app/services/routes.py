"""Persistent, rider-scoped route generation and ordered delivery execution."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.enums import (
    DeliveryRouteStatus,
    FulfillmentStatus,
    NotReceivedReason,
    OrderStatus,
    RouteStopStatus,
)
from app.exceptions import DomainError, not_found
from app.models import DeliveryRoute, Order, RiderActionReceipt, RouteStop
from app.schemas.rider import RiderOrderItemResponse
from app.schemas.routes import (
    AdminRouteDetailResponse,
    AdminRoutePageResponse,
    AdminRouteStopResponse,
    AdminRouteSummaryResponse,
    AdminUnroutedReadyResponse,
    CurrentRouteStopResponse,
    RiderHistoryItemResponse,
    RiderHistoryPageResponse,
    RiderRouteResponse,
    RouteProgressResponse,
    RouteStopPreviewResponse,
    TodaySummaryResponse,
)
from app.services.delivery import KARACHI
from app.services.order_transitions import OrderTransitionService
from app.services.orders import DELIVERY_ORIGIN_LATITUDE, DELIVERY_ORIGIN_LONGITUDE
from app.services.route_optimization import (
    GoogleRouteOptimizer,
    OptimizationStop,
    RouteOptimizer,
    service_account_credentials_from_base64,
)

ACTIVE_ROUTE_STATUSES = {
    DeliveryRouteStatus.GENERATED.value,
    DeliveryRouteStatus.IN_PROGRESS.value,
}
CURRENT_STOP_STATUSES = {RouteStopStatus.READY.value, RouteStopStatus.IN_PROGRESS.value}
COMPLETED_STOP_STATUSES = {
    RouteStopStatus.DELIVERED.value,
    RouteStopStatus.NOT_RECEIVED.value,
}


def _area(address: str) -> str:
    parts = [part.strip() for part in address.split(",") if part.strip()]
    return parts[-2] if len(parts) >= 3 else "Open delivery for address"


def _items(order: Order) -> list[RiderOrderItemResponse]:
    return [
        RiderOrderItemResponse(
            product_name=item.product_name,
            quantity=item.quantity,
            unit_label=item.unit_label,
        )
        for item in order.items
    ]


def _progress(route: DeliveryRoute) -> RouteProgressResponse:
    delivered = sum(stop.status == RouteStopStatus.DELIVERED.value for stop in route.stops)
    not_received = sum(
        stop.status == RouteStopStatus.NOT_RECEIVED.value for stop in route.stops
    )
    current = next((stop for stop in route.stops if stop.status in CURRENT_STOP_STATUSES), None)
    completed = delivered + not_received
    return RouteProgressResponse(
        total=len(route.stops),
        delivered=delivered,
        not_received=not_received,
        completed=completed,
        remaining=len(route.stops) - completed,
        current_sequence=current.sequence if current else None,
    )


def _preview(stop: RouteStop) -> RouteStopPreviewResponse:
    reason = NotReceivedReason(stop.not_received_reason) if stop.not_received_reason else None
    return RouteStopPreviewResponse(
        id=stop.id,
        order_id=stop.order_id,
        order_number=stop.order.order_number,
        sequence=stop.sequence,
        status=RouteStopStatus(stop.status),
        customer_name=stop.order.customer_name_snapshot,
        customer_area=_area(stop.order.delivery_address_snapshot),
        distance_from_previous_meters=stop.distance_from_previous_meters,
        estimated_duration_seconds=stop.estimated_duration_seconds,
        completed_at=stop.completed_at,
        not_received_reason=reason,
        outcome_note=stop.outcome_note,
    )


def _current(stop: RouteStop) -> CurrentRouteStopResponse:
    if stop.order.delivery_latitude is None or stop.order.delivery_longitude is None:
        raise DomainError(500, "route_location_missing", "Current delivery location is missing")
    return CurrentRouteStopResponse(
        **_preview(stop).model_dump(),
        customer_phone=stop.order.customer.phone,
        delivery_address=stop.order.delivery_address_snapshot,
        latitude=stop.order.delivery_latitude,
        longitude=stop.order.delivery_longitude,
        items=_items(stop.order),
        started_at=stop.started_at,
    )


def _admin_stop(stop: RouteStop) -> AdminRouteStopResponse:
    if stop.order.delivery_latitude is None or stop.order.delivery_longitude is None:
        raise DomainError(500, "route_location_missing", "Delivery route stop location is missing")
    return AdminRouteStopResponse(
        **_preview(stop).model_dump(),
        latitude=stop.order.delivery_latitude,
        longitude=stop.order.delivery_longitude,
    )


def rider_route_response(route: DeliveryRoute) -> RiderRouteResponse:
    current = next((stop for stop in route.stops if stop.status in CURRENT_STOP_STATUSES), None)
    visible_stops = (
        route.stops
        if route.status == DeliveryRouteStatus.GENERATED.value
        else [
            stop
            for stop in route.stops
            if stop.status in COMPLETED_STOP_STATUSES | CURRENT_STOP_STATUSES
        ]
    )
    return RiderRouteResponse(
        id=route.id,
        delivery_date=route.delivery_date,
        status=DeliveryRouteStatus(route.status),
        start_source=route.start_source,
        total_distance_meters=route.total_distance_meters,
        estimated_duration_seconds=route.estimated_duration_seconds,
        started_at=route.started_at,
        completed_at=route.completed_at,
        progress=_progress(route),
        preview_stops=[_preview(stop) for stop in visible_stops],
        current_stop=_current(current) if current else None,
        updated_at=route.updated_at,
    )


def admin_route_summary(route: DeliveryRoute) -> AdminRouteSummaryResponse:
    current = next((stop for stop in route.stops if stop.status in CURRENT_STOP_STATUSES), None)
    return AdminRouteSummaryResponse(
        id=route.id,
        rider_id=route.rider_id,
        rider_name=route.rider.name,
        delivery_date=route.delivery_date,
        status=DeliveryRouteStatus(route.status),
        total_distance_meters=route.total_distance_meters,
        estimated_duration_seconds=route.estimated_duration_seconds,
        progress=_progress(route),
        current_order_id=current.order_id if current else None,
        started_at=route.started_at,
        completed_at=route.completed_at,
        created_at=route.created_at,
        updated_at=route.updated_at,
    )


def admin_route_detail(route: DeliveryRoute) -> AdminRouteDetailResponse:
    return AdminRouteDetailResponse(
        **admin_route_summary(route).model_dump(),
        start_latitude=route.start_latitude,
        start_longitude=route.start_longitude,
        start_source=route.start_source,
        stops=[_admin_stop(stop) for stop in route.stops],
    )


class DeliveryRouteService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        optimizer: RouteOptimizer | None = None,
    ) -> None:
        self.session = session
        self.settings = settings
        self.optimizer = optimizer

    def _require_enabled(self) -> None:
        if not self.settings.rider_route_workflow_enabled:
            raise DomainError(409, "route_workflow_disabled", "Rider routes are not enabled")

    def _optimizer(self) -> RouteOptimizer:
        if self.optimizer is not None:
            return self.optimizer
        if not self.settings.google_cloud_project_id:
            raise DomainError(
                503,
                "route_optimization_unconfigured",
                "Route optimization is not configured",
            )
        encoded_credentials = self.settings.google_route_optimization_credentials_base64
        credentials = (
            service_account_credentials_from_base64(encoded_credentials.get_secret_value())
            if encoded_credentials
            else None
        )
        return GoogleRouteOptimizer(
            self.settings.google_cloud_project_id,
            self.settings.route_optimization_timeout_seconds,
            credentials=credentials,
        )

    @staticmethod
    def _route_options() -> tuple[Any, ...]:
        stop_orders = selectinload(DeliveryRoute.stops).selectinload(RouteStop.order)
        return (
            selectinload(DeliveryRoute.rider),
            stop_orders.selectinload(Order.customer),
            selectinload(DeliveryRoute.stops)
            .selectinload(RouteStop.order)
            .selectinload(Order.items),
        )

    async def _load_route(self, route_id: uuid.UUID) -> DeliveryRoute:
        route = await self.session.scalar(
            select(DeliveryRoute)
            .where(DeliveryRoute.id == route_id)
            .options(*self._route_options())
        )
        if route is None:
            raise not_found("Delivery route")
        return route

    async def get_active_route(self, rider_id: uuid.UUID) -> RiderRouteResponse | None:
        route = await self.session.scalar(
            select(DeliveryRoute)
            .where(
                DeliveryRoute.rider_id == rider_id,
                DeliveryRoute.status.in_(ACTIVE_ROUTE_STATUSES),
            )
            .options(*self._route_options())
            .order_by(DeliveryRoute.created_at.desc())
        )
        return rider_route_response(route) if route else None

    async def get_rider_route(
        self, route_id: uuid.UUID, rider_id: uuid.UUID
    ) -> RiderRouteResponse:
        route = await self._load_route(route_id)
        if route.rider_id != rider_id:
            raise DomainError(403, "route_not_assigned", "This route is not assigned to you")
        return rider_route_response(route)

    async def today_summary(self, rider_id: uuid.UUID) -> TodaySummaryResponse:
        today = datetime.now(KARACHI).date()
        orders = list(
            (
                await self.session.scalars(
                    select(Order).where(
                        Order.rider_id == rider_id,
                        Order.promised_delivery_date == today,
                        Order.status.not_in(
                            [OrderStatus.CANCELLED.value, OrderStatus.REFUNDED.value]
                        ),
                    )
                )
            ).all()
        )
        delivered = sum(order.status == OrderStatus.DELIVERED.value for order in orders)
        not_received = sum(order.status == OrderStatus.NOT_RECEIVED.value for order in orders)
        routeable = sum(
            order.status == OrderStatus.PACKING.value
            and order.internal_fulfillment_status == FulfillmentStatus.READY_FOR_DISPATCH.value
            for order in orders
        )
        remaining = len(orders) - delivered - not_received
        return TodaySummaryResponse(
            total=len(orders),
            delivered=delivered,
            not_received=not_received,
            remaining=remaining,
            routeable=routeable,
            blocked=max(0, remaining - routeable),
            active_route=await self.get_active_route(rider_id),
        )

    async def generate_route(
        self,
        *,
        rider_id: uuid.UUID,
        latitude: Decimal | None,
        longitude: Decimal | None,
        use_depot_fallback: bool,
        idempotency_key: uuid.UUID,
    ) -> RiderRouteResponse:
        self._require_enabled()
        today = datetime.now(KARACHI).date()
        start_latitude = DELIVERY_ORIGIN_LATITUDE if use_depot_fallback else latitude
        start_longitude = DELIVERY_ORIGIN_LONGITUDE if use_depot_fallback else longitude
        if start_latitude is None or start_longitude is None:
            raise DomainError(422, "route_start_required", "A route start location is required")

        request_hash = self._hash(
            "generate",
            {
                "latitude": start_latitude,
                "longitude": start_longitude,
                "use_depot_fallback": use_depot_fallback,
            },
        )
        receipt_result = await self._existing_receipt(
            rider_id=rider_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if receipt_result is not None:
            return receipt_result

        existing = await self.session.scalar(
            select(DeliveryRoute).where(
                DeliveryRoute.rider_id == rider_id,
                DeliveryRoute.delivery_date == today,
                DeliveryRoute.status.in_(ACTIVE_ROUTE_STATUSES),
            )
        )
        if existing is not None:
            return rider_route_response(await self._load_route(existing.id))

        candidates = list(
            (
                await self.session.scalars(
                    select(Order)
                    .where(
                        Order.rider_id == rider_id,
                        Order.promised_delivery_date == today,
                        Order.status == OrderStatus.PACKING.value,
                        Order.internal_fulfillment_status
                        == FulfillmentStatus.READY_FOR_DISPATCH.value,
                    )
                    .order_by(Order.created_at, Order.id)
                )
            ).all()
        )
        missing = [order.id for order in candidates if order.delivery_latitude is None]
        if missing:
            raise DomainError(
                422,
                "route_delivery_location_missing",
                "Some ready deliveries do not have valid coordinates",
                fields=[{"order_id": str(order_id)} for order_id in missing],
            )
        if not candidates:
            raise DomainError(409, "no_route_deliveries", "No deliveries are ready for routing")

        candidate_ids = [order.id for order in candidates]
        optimization_stops = [
            OptimizationStop(
                order_id=order.id,
                latitude=order.delivery_latitude,  # type: ignore[arg-type]
                longitude=order.delivery_longitude,  # type: ignore[arg-type]
            )
            for order in candidates
        ]
        if self.session.in_transaction():
            await self.session.rollback()
        optimized = await self._optimizer().optimize(
            delivery_date=today,
            start_latitude=start_latitude,
            start_longitude=start_longitude,
            stops=optimization_stops,
        )
        if (
            len(optimized.legs) != len(candidate_ids)
            or {leg.order_id for leg in optimized.legs} != set(candidate_ids)
        ):
            raise DomainError(
                502,
                "route_optimization_invalid",
                "The route provider returned an invalid delivery sequence",
            )

        route_id = uuid.uuid4()
        try:
            async with self.session.begin():
                locked = list(
                    (
                        await self.session.scalars(
                            select(Order)
                            .where(Order.id.in_(candidate_ids))
                            .order_by(Order.created_at, Order.id)
                            .with_for_update()
                        )
                    ).all()
                )
                if len(locked) != len(candidate_ids) or any(
                    order.rider_id != rider_id
                    or order.promised_delivery_date != today
                    or order.status != OrderStatus.PACKING.value
                    or order.internal_fulfillment_status
                    != FulfillmentStatus.READY_FOR_DISPATCH.value
                    for order in locked
                ):
                    raise DomainError(
                        409,
                        "route_deliveries_changed",
                        "Today's ready deliveries changed; generate the route again",
                    )
                route = DeliveryRoute(
                    id=route_id,
                    rider_id=rider_id,
                    delivery_date=today,
                    status=DeliveryRouteStatus.GENERATED.value,
                    start_latitude=start_latitude,
                    start_longitude=start_longitude,
                    start_source="depot" if use_depot_fallback else "gps",
                    total_distance_meters=optimized.total_distance_meters,
                    estimated_duration_seconds=optimized.total_duration_seconds,
                )
                self.session.add(route)
                for sequence, leg in enumerate(optimized.legs, start=1):
                    self.session.add(
                        RouteStop(
                            route_id=route_id,
                            order_id=leg.order_id,
                            sequence=sequence,
                            status=RouteStopStatus.PENDING.value,
                            distance_from_previous_meters=leg.distance_meters,
                            estimated_duration_seconds=leg.duration_seconds,
                        )
                    )
                self.session.add(
                    RiderActionReceipt(
                        idempotency_key=idempotency_key,
                        request_hash=request_hash,
                        rider_id=rider_id,
                        route_id=route_id,
                        action="generate",
                    )
                )
        except IntegrityError as error:
            raise DomainError(
                409, "route_already_exists", "An active route already exists"
            ) from error
        return rider_route_response(await self._load_route(route_id))

    @staticmethod
    def _hash(action: str, payload: object) -> str:
        canonical = json.dumps(
            {"action": action, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    async def _existing_receipt(
        self,
        *,
        rider_id: uuid.UUID,
        idempotency_key: uuid.UUID,
        request_hash: str,
    ) -> RiderRouteResponse | None:
        receipt = await self.session.get(RiderActionReceipt, idempotency_key)
        if receipt is None:
            return None
        if receipt.rider_id != rider_id or receipt.request_hash != request_hash:
            raise DomainError(
                409,
                "idempotency_key_reused",
                "Idempotency key was already used with different input",
            )
        return await self.get_rider_route(receipt.route_id, rider_id)

    async def start_route(
        self,
        route_id: uuid.UUID,
        rider_id: uuid.UUID,
        idempotency_key: uuid.UUID,
    ) -> RiderRouteResponse:
        request_hash = self._hash("start_route", None)
        existing = await self._existing_receipt(
            rider_id=rider_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing:
            return existing
        if self.session.in_transaction():
            await self.session.rollback()
        async with self.session.begin():
            route = await self.session.scalar(
                select(DeliveryRoute).where(DeliveryRoute.id == route_id).with_for_update()
            )
            if route is None:
                raise not_found("Delivery route")
            if route.rider_id != rider_id:
                raise DomainError(403, "route_not_assigned", "This route is not assigned to you")
            if route.status != DeliveryRouteStatus.GENERATED.value:
                raise DomainError(409, "route_not_startable", "Route cannot be started")
            first = await self.session.scalar(
                select(RouteStop)
                .where(RouteStop.route_id == route.id)
                .order_by(RouteStop.sequence)
                .with_for_update()
            )
            if first is None:
                raise DomainError(409, "route_empty", "Route has no delivery stops")
            now = datetime.now(UTC)
            route.status = DeliveryRouteStatus.IN_PROGRESS.value
            route.started_at = now
            first.status = RouteStopStatus.READY.value
            self.session.add(
                RiderActionReceipt(
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    rider_id=rider_id,
                    route_id=route.id,
                    action="start_route",
                )
            )
        return rider_route_response(await self._load_route(route_id))

    async def start_stop(
        self,
        route_id: uuid.UUID,
        stop_id: uuid.UUID,
        rider_id: uuid.UUID,
        idempotency_key: uuid.UUID,
    ) -> RiderRouteResponse:
        request_hash = self._hash("start_stop", {"stop_id": stop_id})
        existing = await self._existing_receipt(
            rider_id=rider_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing:
            return existing
        if self.session.in_transaction():
            await self.session.rollback()
        async with self.session.begin():
            route, stop = await self._lock_route_stop(route_id, stop_id, rider_id)
            if (
                route.status != DeliveryRouteStatus.IN_PROGRESS.value
                or stop.status != RouteStopStatus.READY.value
            ):
                raise DomainError(409, "stop_not_startable", "Only the current stop can start")
            await OrderTransitionService(self.session).dispatch_for_rider_in_transaction(
                stop.order_id, rider_id
            )
            stop.status = RouteStopStatus.IN_PROGRESS.value
            stop.started_at = datetime.now(UTC)
            self._add_receipt(
                idempotency_key, request_hash, rider_id, route.id, stop.id, "start_stop"
            )
        return rider_route_response(await self._load_route(route_id))

    async def complete_stop(
        self,
        *,
        route_id: uuid.UUID,
        stop_id: uuid.UUID,
        rider_id: uuid.UUID,
        idempotency_key: uuid.UUID,
        delivered: bool,
        reason: NotReceivedReason | None = None,
        note: str | None = None,
    ) -> RiderRouteResponse:
        action = "delivered" if delivered else "not_received"
        payload = {"stop_id": stop_id, "reason": reason, "note": note}
        request_hash = self._hash(action, payload)
        existing = await self._existing_receipt(
            rider_id=rider_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if existing:
            return existing
        if self.session.in_transaction():
            await self.session.rollback()
        async with self.session.begin():
            route, stop = await self._lock_route_stop(route_id, stop_id, rider_id)
            if (
                route.status != DeliveryRouteStatus.IN_PROGRESS.value
                or stop.status != RouteStopStatus.IN_PROGRESS.value
            ):
                raise DomainError(409, "stop_not_completable", "Only the active stop can finish")
            transitions = OrderTransitionService(self.session)
            if delivered:
                await transitions.deliver_order_in_transaction(
                    stop.order_id, None, assigned_rider_id=rider_id
                )
                stop.status = RouteStopStatus.DELIVERED.value
            else:
                if reason is None:
                    raise DomainError(
                        422, "not_received_reason_required", "A not-received reason is required"
                    )
                outcome_note = note or reason.value.replace("_", " ")
                await transitions.mark_not_received_in_transaction(
                    stop.order_id,
                    None,
                    outcome_note,
                    assigned_rider_id=rider_id,
                )
                stop.status = RouteStopStatus.NOT_RECEIVED.value
                stop.not_received_reason = reason.value
                stop.outcome_note = note
            stop.completed_at = datetime.now(UTC)
            # The database permits only one ready/in-progress stop per route. Flush the
            # completed current stop before promoting the next one so PostgreSQL never
            # observes both rows as current during the same transaction.
            await self.session.flush([stop])
            next_stop = await self.session.scalar(
                select(RouteStop)
                .where(
                    RouteStop.route_id == route.id,
                    RouteStop.status == RouteStopStatus.PENDING.value,
                )
                .order_by(RouteStop.sequence)
                .with_for_update()
            )
            if next_stop is None:
                route.status = DeliveryRouteStatus.COMPLETED.value
                route.completed_at = datetime.now(UTC)
            else:
                next_stop.status = RouteStopStatus.READY.value
            self._add_receipt(
                idempotency_key, request_hash, rider_id, route.id, stop.id, action
            )
        return rider_route_response(await self._load_route(route_id))

    async def _lock_route_stop(
        self, route_id: uuid.UUID, stop_id: uuid.UUID, rider_id: uuid.UUID
    ) -> tuple[DeliveryRoute, RouteStop]:
        route = await self.session.scalar(
            select(DeliveryRoute).where(DeliveryRoute.id == route_id).with_for_update()
        )
        if route is None:
            raise not_found("Delivery route")
        if route.rider_id != rider_id:
            raise DomainError(403, "route_not_assigned", "This route is not assigned to you")
        stop = await self.session.scalar(
            select(RouteStop)
            .where(RouteStop.id == stop_id, RouteStop.route_id == route.id)
            .with_for_update()
        )
        if stop is None:
            raise not_found("Route stop")
        return route, stop

    def _add_receipt(
        self,
        key: uuid.UUID,
        request_hash: str,
        rider_id: uuid.UUID,
        route_id: uuid.UUID,
        stop_id: uuid.UUID | None,
        action: str,
    ) -> None:
        self.session.add(
            RiderActionReceipt(
                idempotency_key=key,
                request_hash=request_hash,
                rider_id=rider_id,
                route_id=route_id,
                stop_id=stop_id,
                action=action,
            )
        )

    async def history(
        self,
        *,
        rider_id: uuid.UUID,
        date_from: date | None,
        date_to: date | None,
        status: OrderStatus | None,
        query: str | None,
        page: int,
        page_size: int,
    ) -> RiderHistoryPageResponse:
        today = datetime.now(KARACHI).date()
        effective_to = date_to or today
        effective_from = date_from or (effective_to - timedelta(days=30))
        if effective_from > effective_to:
            raise DomainError(
                422, "invalid_history_range", "History start date must be before end date"
            )
        if (effective_to - effective_from).days > 90:
            raise DomainError(
                422, "history_range_too_large", "History date range cannot exceed 90 days"
            )
        if status and status not in {
            OrderStatus.DELIVERED,
            OrderStatus.NOT_RECEIVED,
            OrderStatus.CANCELLED,
        }:
            raise DomainError(
                422,
                "invalid_history_status",
                "History status must be delivered, not received, or cancelled",
            )
        conditions = [
            Order.rider_id == rider_id,
            Order.status.in_(
                [
                    OrderStatus.DELIVERED.value,
                    OrderStatus.NOT_RECEIVED.value,
                    OrderStatus.CANCELLED.value,
                ]
            ),
        ]
        conditions.append(Order.promised_delivery_date >= effective_from)
        conditions.append(Order.promised_delivery_date <= effective_to)
        if status:
            conditions.append(Order.status == status.value)
        if query and query.strip():
            term = f"%{query.strip()}%"
            conditions.append(
                or_(
                    Order.order_number.ilike(term),
                    cast(Order.id, String).ilike(term),
                    Order.customer_name_snapshot.ilike(term),
                )
            )
        total = int(
            await self.session.scalar(select(func.count(Order.id)).where(*conditions)) or 0
        )
        latest_stop_id = (
            select(RouteStop.id)
            .where(RouteStop.order_id == Order.id)
            .order_by(RouteStop.updated_at.desc(), RouteStop.created_at.desc())
            .limit(1)
            .correlate(Order)
            .scalar_subquery()
        )
        rows = (
            await self.session.execute(
                select(Order, RouteStop)
                .outerjoin(RouteStop, RouteStop.id == latest_stop_id)
                .where(*conditions)
                .options(selectinload(Order.items))
                .order_by(Order.promised_delivery_date.desc(), Order.updated_at.desc(), Order.id)
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        items = [self._history_item(order, stop) for order, stop in rows]
        return RiderHistoryPageResponse(
            items=items, page=page, page_size=page_size, total=total
        )

    @staticmethod
    def _history_item(order: Order, stop: RouteStop | None) -> RiderHistoryItemResponse:
        reason = (
            NotReceivedReason(stop.not_received_reason)
            if stop and stop.not_received_reason
            else None
        )
        return RiderHistoryItemResponse(
            id=order.id,
            order_number=order.order_number,
            status=OrderStatus(order.status),
            customer_name=order.customer_name_snapshot,
            customer_area=_area(order.delivery_address_snapshot),
            items=_items(order),
            outcome_at=stop.completed_at if stop and stop.completed_at else order.updated_at,
            not_received_reason=reason,
            outcome_note=stop.outcome_note if stop else None,
        )

    async def history_detail(
        self, order_id: uuid.UUID, rider_id: uuid.UUID
    ) -> RiderHistoryItemResponse:
        latest_stop_id = (
            select(RouteStop.id)
            .where(RouteStop.order_id == Order.id)
            .order_by(RouteStop.updated_at.desc(), RouteStop.created_at.desc())
            .limit(1)
            .correlate(Order)
            .scalar_subquery()
        )
        row = (
            await self.session.execute(
                select(Order, RouteStop)
                .outerjoin(RouteStop, RouteStop.id == latest_stop_id)
                .where(Order.id == order_id)
                .options(selectinload(Order.items))
            )
        ).first()
        if row is None:
            raise not_found("Delivery")
        order, stop = row
        if order.rider_id != rider_id:
            raise DomainError(403, "order_not_assigned", "This order is not assigned to you")
        if order.status not in {
            OrderStatus.DELIVERED.value,
            OrderStatus.NOT_RECEIVED.value,
            OrderStatus.CANCELLED.value,
        }:
            raise DomainError(409, "delivery_not_historical", "Delivery is not in history")
        return self._history_item(order, stop)


class AdminRouteService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def unrouted_ready(self, delivery_date: date | None) -> AdminUnroutedReadyResponse:
        on_date = delivery_date or datetime.now(KARACHI).date()
        unresolved_stop = (
            select(RouteStop.id)
            .where(
                RouteStop.order_id == Order.id,
                RouteStop.status.in_(
                    [
                        RouteStopStatus.PENDING.value,
                        RouteStopStatus.READY.value,
                        RouteStopStatus.IN_PROGRESS.value,
                    ]
                ),
            )
            .correlate(Order)
            .exists()
        )
        conditions = [
            Order.promised_delivery_date == on_date,
            Order.rider_id.is_not(None),
            Order.status == OrderStatus.PACKING.value,
            Order.internal_fulfillment_status
            == FulfillmentStatus.READY_FOR_DISPATCH.value,
            ~unresolved_stop,
        ]
        total = int(
            await self.session.scalar(select(func.count(Order.id)).where(*conditions)) or 0
        )
        order_ids = list(
            (
                await self.session.scalars(
                    select(Order.id)
                    .where(*conditions)
                    .order_by(Order.created_at)
                    .limit(100)
                )
            ).all()
        )
        return AdminUnroutedReadyResponse(total=total, order_ids=order_ids)

    async def list_routes(
        self,
        *,
        delivery_date: date | None,
        rider_id: uuid.UUID | None,
        status: DeliveryRouteStatus | None,
        page: int,
        page_size: int,
    ) -> AdminRoutePageResponse:
        conditions = []
        if delivery_date:
            conditions.append(DeliveryRoute.delivery_date == delivery_date)
        if rider_id:
            conditions.append(DeliveryRoute.rider_id == rider_id)
        if status:
            conditions.append(DeliveryRoute.status == status.value)
        total = int(
            await self.session.scalar(select(func.count(DeliveryRoute.id)).where(*conditions))
            or 0
        )
        routes = list(
            (
                await self.session.scalars(
                    select(DeliveryRoute)
                    .where(*conditions)
                    .options(*DeliveryRouteService._route_options())
                    .order_by(DeliveryRoute.delivery_date.desc(), DeliveryRoute.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return AdminRoutePageResponse(
            items=[admin_route_summary(route) for route in routes],
            page=page,
            page_size=page_size,
            total=total,
        )

    async def get_route(self, route_id: uuid.UUID) -> AdminRouteDetailResponse:
        route = await self.session.scalar(
            select(DeliveryRoute)
            .where(DeliveryRoute.id == route_id)
            .options(*DeliveryRouteService._route_options())
        )
        if route is None:
            raise not_found("Delivery route")
        return admin_route_detail(route)

    async def cancel_generated(self, route_id: uuid.UUID) -> AdminRouteDetailResponse:
        if self.session.in_transaction():
            await self.session.rollback()
        async with self.session.begin():
            route = await self.session.scalar(
                select(DeliveryRoute).where(DeliveryRoute.id == route_id).with_for_update()
            )
            if route is None:
                raise not_found("Delivery route")
            if route.status != DeliveryRouteStatus.GENERATED.value:
                raise DomainError(
                    409, "route_not_cancellable", "Only a generated route can be cancelled"
                )
            stops = list(
                (
                    await self.session.scalars(
                        select(RouteStop)
                        .where(RouteStop.route_id == route.id)
                        .with_for_update()
                    )
                ).all()
            )
            route.status = DeliveryRouteStatus.CANCELLED.value
            route.cancelled_at = datetime.now(UTC)
            for stop in stops:
                if stop.status in {
                    RouteStopStatus.PENDING.value,
                    RouteStopStatus.READY.value,
                }:
                    stop.status = RouteStopStatus.CANCELLED.value
        return await self.get_route(route_id)
