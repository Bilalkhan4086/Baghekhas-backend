"""Atomic order operations built on the inventory service layer."""

from __future__ import annotations

import uuid
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    FulfillmentStatus,
    InventoryReservationStatus,
    OrderStatus,
    StockPolicy,
)
from app.exceptions import DomainError, not_found
from app.models import (
    InventoryReservation,
    Order,
    OrderFulfillmentLine,
    OrderItem,
    OrderStatusHistory,
    Product,
    Rider,
)
from app.services.delivery import RiderAssignmentService
from app.services.inventory import ReservationService


class OrderTransitionService:
    """Owns each order transition and its inventory effects in one transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _locked_order(self, order_id: uuid.UUID) -> Order:
        order = await self.session.scalar(
            select(Order).where(Order.id == order_id).with_for_update()
        )
        if order is None:
            raise not_found("Order")
        return order

    def _history(
        self, order: Order, old: str | None, note: str | None, admin_id: uuid.UUID | None
    ) -> None:
        self.session.add(
            OrderStatusHistory(
                order_id=order.id,
                from_status=old,
                to_status=order.status,
                note=note,
                actor_admin_id=admin_id,
            )
        )

    @staticmethod
    def available_actions(order: Order) -> list[str]:
        """Return actions enabled by the same state checks used by transition methods."""
        actions: list[str] = []
        if order.status == OrderStatus.PENDING.value:
            actions.extend(["confirm", "cancel"])
        elif order.status == OrderStatus.CONFIRMED.value:
            actions.append("cancel")
            if order.internal_fulfillment_status == FulfillmentStatus.STOCK_AVAILABLE.value:
                actions.append("start_packing")
            elif order.internal_fulfillment_status == FulfillmentStatus.PROCUREMENT_REQUIRED.value:
                actions.append("start_procurement")
            elif (
                order.internal_fulfillment_status
                == FulfillmentStatus.PROCUREMENT_IN_PROGRESS.value
            ):
                actions.append("mark_procured")
        elif order.status == OrderStatus.PACKING.value:
            actions.extend(["dispatch", "cancel"])
        elif order.status == OrderStatus.DISPATCHED.value:
            actions.extend(["deliver", "not_received"])
        return actions

    async def confirm_order(self, order_id: uuid.UUID, admin_id: uuid.UUID) -> Order:
        """Atomically validate prices, reserve stock, and confirm one pending order."""
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if order.status != OrderStatus.PENDING.value:
                raise DomainError(
                    409, "invalid_status_transition", "Only pending orders can be confirmed"
                )
            items = list(
                (
                    await self.session.scalars(
                        select(OrderItem).where(OrderItem.order_id == order.id)
                    )
                ).all()
            )
            reservations = ReservationService(self.session)
            shortages = False
            for item in items:
                product = await self.session.get(Product, item.product_id)
                if (
                    product is None
                    or product.name != item.product_name
                    or product.base_price_pkr != item.unit_price_pkr
                ):
                    raise DomainError(
                        409, "stale_order_item", f"Catalog price changed for {item.product_id}"
                    )
                result = await reservations.reserve_stock_in_transaction(
                    product_id=item.product_id, quantity=item.quantity, order_id=order.id
                )
                policy = product.effective_stock_policy
                if result.shortage_quantity > 0 and policy == StockPolicy.IN_STOCK_ONLY.value:
                    raise DomainError(
                        409, "insufficient_stock", f"Insufficient stock for {item.product_id}"
                    )
                line_status = FulfillmentStatus.STOCK_AVAILABLE
                if result.shortage_quantity > 0:
                    shortages = True
                    line_status = FulfillmentStatus.PROCUREMENT_REQUIRED
                self.session.add(
                    OrderFulfillmentLine(
                        order_id=order.id,
                        order_item_id=item.id,
                        requested_quantity=item.quantity,
                        reserved_quantity=result.reserved_quantity,
                        procurement_quantity=result.shortage_quantity,
                        status=line_status.value,
                        cogs=Decimal("0"),
                    )
                )
            old = order.status
            order.status = OrderStatus.CONFIRMED.value
            order.internal_fulfillment_status = (
                FulfillmentStatus.PROCUREMENT_REQUIRED
                if shortages
                else FulfillmentStatus.STOCK_AVAILABLE
            ).value
            self._history(order, old, "Order confirmed", admin_id)
        return order

    async def start_packing(self, order_id: uuid.UUID, admin_id: uuid.UUID) -> Order:
        """Atomically move a fully stocked confirmed order into packing."""
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if (
                order.status != OrderStatus.CONFIRMED.value
                or order.internal_fulfillment_status != FulfillmentStatus.STOCK_AVAILABLE.value
            ):
                raise DomainError(
                    409, "invalid_status_transition", "Order is not ready for packing"
                )
            old = order.status
            order.status = OrderStatus.PACKING.value
            order.internal_fulfillment_status = FulfillmentStatus.READY_FOR_DISPATCH.value
            self._history(order, old, "Packing started", admin_id)
        return order

    async def mark_procurement_in_progress(self, order_id: uuid.UUID, admin_id: uuid.UUID) -> Order:
        """Atomically mark a confirmed shortage as being sourced; customer status is unchanged."""
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if (
                order.status != OrderStatus.CONFIRMED.value
                or order.internal_fulfillment_status != FulfillmentStatus.PROCUREMENT_REQUIRED.value
            ):
                raise DomainError(
                    409, "invalid_fulfillment_transition", "Order has no procurement requirement"
                )
            lines = list(
                (
                    await self.session.scalars(
                        select(OrderFulfillmentLine).where(
                            OrderFulfillmentLine.order_id == order.id
                        )
                    )
                ).all()
            )
            for line in lines:
                if line.procurement_quantity > 0:
                    line.status = FulfillmentStatus.PROCUREMENT_IN_PROGRESS.value
            order.internal_fulfillment_status = FulfillmentStatus.PROCUREMENT_IN_PROGRESS.value
            self._history(order, order.status, "Procurement started", admin_id)
        return order

    async def mark_procured(self, order_id: uuid.UUID, admin_id: uuid.UUID) -> Order:
        """Atomically re-reserve outstanding quantities after a purchase receipt."""
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if (
                order.status != OrderStatus.CONFIRMED.value
                or order.internal_fulfillment_status
                not in {
                    FulfillmentStatus.PROCUREMENT_REQUIRED.value,
                    FulfillmentStatus.PROCUREMENT_IN_PROGRESS.value,
                }
            ):
                raise DomainError(
                    409, "invalid_fulfillment_transition", "Order is not awaiting procurement"
                )
            lines = list(
                (
                    await self.session.scalars(
                        select(OrderFulfillmentLine).where(
                            OrderFulfillmentLine.order_id == order.id
                        )
                    )
                ).all()
            )
            items = {
                item.id: item
                for item in (
                    await self.session.scalars(
                        select(OrderItem).where(OrderItem.order_id == order.id)
                    )
                ).all()
            }
            reservations = ReservationService(self.session)
            incomplete = False
            for line in lines:
                if line.procurement_quantity <= 0:
                    continue
                item = items[line.order_item_id]
                result = await reservations.reserve_stock_in_transaction(
                    product_id=item.product_id,
                    quantity=line.procurement_quantity,
                    order_id=order.id,
                )
                line.reserved_quantity += result.reserved_quantity
                line.procurement_quantity = result.shortage_quantity
                line.status = (
                    FulfillmentStatus.PROCUREMENT_REQUIRED
                    if result.shortage_quantity > 0
                    else FulfillmentStatus.STOCK_AVAILABLE
                ).value
                incomplete = incomplete or result.shortage_quantity > 0
            order.internal_fulfillment_status = (
                FulfillmentStatus.PROCUREMENT_REQUIRED
                if incomplete
                else FulfillmentStatus.STOCK_AVAILABLE
            ).value
            self._history(order, order.status, "Procurement rechecked", admin_id)
        return order

    async def dispatch_order(
        self, order_id: uuid.UUID, rider_id: uuid.UUID | None, admin_id: uuid.UUID
    ) -> Order:
        """Atomically assign an active rider and dispatch a packed order."""
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if (
                order.status != OrderStatus.PACKING.value
                or order.internal_fulfillment_status != FulfillmentStatus.READY_FOR_DISPATCH.value
            ):
                raise DomainError(
                    409, "invalid_status_transition", "Order is not ready for dispatch"
                )
            assignment = RiderAssignmentService(self.session)
            rider: Rider | None
            if rider_id is not None:
                rider = await assignment.require_active_rider(rider_id)
            elif order.rider_id is not None:
                rider = await assignment.require_active_rider(order.rider_id)
            else:
                rider = await assignment.select_rider_for_order(order)
            if rider is None:
                raise DomainError(
                    409,
                    "rider_unavailable",
                    "No active rider is available for this order",
                )
            old = order.status
            order.status = OrderStatus.DISPATCHED.value
            order.rider_id = rider.id
            self._history(order, old, "Order dispatched", admin_id)
        return order

    async def start_rider_delivery(self, order_id: uuid.UUID, rider_id: uuid.UUID) -> Order:
        """Record one rider's pickup/loading time without changing customer status."""
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if order.rider_id != rider_id:
                raise DomainError(403, "order_not_assigned", "This order is not assigned to you")
            if order.status != OrderStatus.DISPATCHED.value:
                raise DomainError(
                    409, "invalid_status_transition", "Only dispatched orders can be started"
                )
            if order.rider_started_at is None:
                from datetime import UTC, datetime

                order.rider_started_at = datetime.now(UTC)
                self._history(order, order.status, "Rider started delivery", None)
        return order

    async def deliver_order(
        self,
        order_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        *,
        assigned_rider_id: uuid.UUID | None = None,
    ) -> Order:
        """Atomically consume active reservations, persist FIFO COGS, and deliver an order."""
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if assigned_rider_id is not None and order.rider_id != assigned_rider_id:
                raise DomainError(403, "order_not_assigned", "This order is not assigned to you")
            if assigned_rider_id is not None and order.status == OrderStatus.DELIVERED.value:
                return order
            if order.status != OrderStatus.DISPATCHED.value:
                raise DomainError(
                    409, "invalid_status_transition", "Only dispatched orders can be delivered"
                )
            reservations = list(
                (
                    await self.session.scalars(
                        select(InventoryReservation).where(
                            InventoryReservation.order_id == order.id,
                            InventoryReservation.status == InventoryReservationStatus.ACTIVE.value,
                        )
                    )
                ).all()
            )
            reservation_service = ReservationService(self.session)
            total = Decimal("0")
            for reservation in reservations:
                total += await reservation_service.record_sale_in_transaction(
                    reservation.id
                ) or Decimal("0")
            order.cogs_pkr = int(total.quantize(Decimal("1"), ROUND_HALF_UP))
            old = order.status
            order.status = OrderStatus.DELIVERED.value
            self._history(order, old, "Order delivered", actor_id)
        return order

    async def cancel_order(self, order_id: uuid.UUID, admin_id: uuid.UUID, reason: str) -> Order:
        """Atomically release active reservations and cancel a pre-dispatch order."""
        if not reason.strip():
            raise DomainError(
                422, "cancellation_reason_required", "A cancellation reason is required"
            )
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if order.status not in {
                OrderStatus.PENDING.value,
                OrderStatus.CONFIRMED.value,
                OrderStatus.PACKING.value,
            }:
                raise DomainError(409, "invalid_status_transition", "Order cannot be cancelled now")
            rows = list(
                (
                    await self.session.scalars(
                        select(InventoryReservation.id).where(
                            InventoryReservation.order_id == order.id,
                            InventoryReservation.status == InventoryReservationStatus.ACTIVE.value,
                        )
                    )
                ).all()
            )
            service = ReservationService(self.session)
            for reservation_id in rows:
                await service.release_reservation_in_transaction(reservation_id)
            old = order.status
            order.status = OrderStatus.CANCELLED.value
            self._history(order, old, reason.strip(), admin_id)
        return order

    async def mark_not_received(
        self,
        order_id: uuid.UUID,
        admin_id: uuid.UUID | None,
        notes: str,
        *,
        assigned_rider_id: uuid.UUID | None = None,
    ) -> Order:
        """Atomically flag a dispatched order for manual resolution without releasing stock."""
        if not notes.strip():
            raise DomainError(422, "not_received_notes_required", "Resolution notes are required")
        async with self.session.begin():
            order = await self._locked_order(order_id)
            if assigned_rider_id is not None and order.rider_id != assigned_rider_id:
                raise DomainError(403, "order_not_assigned", "This order is not assigned to you")
            if assigned_rider_id is not None and order.status == OrderStatus.NOT_RECEIVED.value:
                return order
            if order.status != OrderStatus.DISPATCHED.value:
                raise DomainError(
                    409,
                    "invalid_status_transition",
                    "Only dispatched orders can be marked not received",
                )
            old = order.status
            order.status = OrderStatus.NOT_RECEIVED.value
            self._history(order, old, notes.strip(), admin_id)
        return order
