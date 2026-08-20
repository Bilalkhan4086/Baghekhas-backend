"""Read-only procurement demand projection.

This module keeps pending-order and low-stock planning separate from inventory mutations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import FulfillmentStatus, InventoryMode, OrderStatus, PublicationStatus
from app.exceptions import DomainError, not_found
from app.models import ManualProcurementItem, Order, OrderFulfillmentLine, OrderItem, Product
from app.schemas.inventory import (
    ManualProcurementItemResponse,
    ProcurementRequirementResponse,
)

ZERO = Decimal("0")


@dataclass
class _ProcurementDemand:
    confirmed_shortage: Decimal = ZERO
    pending_quantity: Decimal = ZERO
    confirmed_order_ids: list[uuid.UUID] = field(default_factory=list)
    pending_order_ids: list[uuid.UUID] = field(default_factory=list)
    procurement_in_progress: bool = False


@dataclass(frozen=True)
class ProcurementProjection:
    projected_stock: Decimal
    order_shortage: Decimal
    low_stock_replenishment: Decimal
    suggested_purchase: Decimal


def project_procurement(
    *,
    current_stock: Decimal,
    low_stock_threshold: Decimal,
    confirmed_shortage: Decimal,
    pending_quantity: Decimal,
) -> ProcurementProjection:
    """Project order coverage and the quantity needed to restore the stock buffer."""
    outstanding_order_demand = confirmed_shortage + pending_quantity
    projected_stock = max(current_stock - outstanding_order_demand, ZERO)
    order_shortage = max(outstanding_order_demand - current_stock, ZERO)
    low_stock_replenishment = max(low_stock_threshold - projected_stock, ZERO)
    return ProcurementProjection(
        projected_stock=projected_stock,
        order_shortage=order_shortage,
        low_stock_replenishment=low_stock_replenishment,
        suggested_purchase=order_shortage + low_stock_replenishment,
    )


class ProcurementReadService:
    """Build the procurement buying list without mutating fulfillment or inventory."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def requirements(self) -> list[ProcurementRequirementResponse]:
        manual_items = list(
            (
                await self.session.scalars(
                    select(ManualProcurementItem)
                )
            ).all()
        )
        manual_by_product = {item.product_id: item for item in manual_items}
        fulfillment_rows = (
            await self.session.execute(
                select(OrderFulfillmentLine, OrderItem)
                .join(OrderItem, OrderItem.id == OrderFulfillmentLine.order_item_id)
                .where(
                    OrderFulfillmentLine.status.in_(
                        {
                            FulfillmentStatus.PROCUREMENT_REQUIRED.value,
                            FulfillmentStatus.PROCUREMENT_IN_PROGRESS.value,
                        }
                    ),
                    OrderFulfillmentLine.procurement_quantity > ZERO,
                )
                .order_by(OrderFulfillmentLine.order_id.asc())
            )
        ).all()
        demand_by_product: dict[str, _ProcurementDemand] = {}
        for line, item in fulfillment_rows:
            demand = demand_by_product.setdefault(item.product_id, _ProcurementDemand())
            demand.confirmed_shortage += line.procurement_quantity
            if line.order_id not in demand.confirmed_order_ids:
                demand.confirmed_order_ids.append(line.order_id)
            if line.status == FulfillmentStatus.PROCUREMENT_IN_PROGRESS.value:
                demand.procurement_in_progress = True

        pending_rows = (
            await self.session.execute(
                select(OrderItem, Order)
                .join(Order, Order.id == OrderItem.order_id)
                .where(Order.status == OrderStatus.PENDING.value)
                .order_by(Order.created_at.asc(), Order.id.asc(), OrderItem.id.asc())
            )
        ).all()
        for item, order in pending_rows:
            demand = demand_by_product.setdefault(item.product_id, _ProcurementDemand())
            demand.pending_quantity += item.quantity
            if order.id not in demand.pending_order_ids:
                demand.pending_order_ids.append(order.id)

        products = (
            await self.session.scalars(
                select(Product)
                .where(
                    or_(
                        Product.inventory_mode == InventoryMode.TRACKED.value,
                        Product.id.in_(list(manual_by_product)),
                    )
                )
                .order_by(Product.name.asc(), Product.id.asc())
            )
        ).all()
        requirements: list[ProcurementRequirementResponse] = []
        for product in products:
            demand = demand_by_product.get(product.id, _ProcurementDemand())
            manual_item = manual_by_product.get(product.id)
            manual_quantity = manual_item.quantity if manual_item is not None else ZERO
            projection = project_procurement(
                current_stock=product.stock_quantity,
                low_stock_threshold=product.low_stock_threshold,
                confirmed_shortage=demand.confirmed_shortage,
                pending_quantity=demand.pending_quantity,
            )
            should_replenish = (
                product.publication_status != PublicationStatus.ARCHIVED.value
                and projection.projected_stock <= product.low_stock_threshold
            )
            if (
                projection.order_shortage <= ZERO
                and not should_replenish
                and manual_quantity <= ZERO
            ):
                continue

            order_ids = list(demand.confirmed_order_ids)
            order_ids.extend(
                order_id for order_id in demand.pending_order_ids if order_id not in order_ids
            )
            requirements.append(
                ProcurementRequirementResponse(
                    product_id=product.id,
                    product_name=product.name,
                    unit_label=product.unit_label,
                    current_stock_quantity=product.stock_quantity,
                    projected_stock_quantity=projection.projected_stock,
                    pending_order_quantity=demand.pending_quantity,
                    shortage_quantity=projection.order_shortage,
                    low_stock_replenishment_quantity=projection.low_stock_replenishment,
                    suggested_purchase_quantity=projection.suggested_purchase + manual_quantity,
                    low_stock_threshold=product.low_stock_threshold,
                    affected_order_count=len(order_ids),
                    pending_order_count=len(demand.pending_order_ids),
                    procurement_in_progress=demand.procurement_in_progress,
                    manual_quantity=manual_quantity,
                    manual_note=manual_item.note if manual_item is not None else None,
                    order_ids=order_ids,
                )
            )
        return requirements


class ManualProcurementService:
    """Persist an administrator's extra quantity on the generated buying list."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def set_item(
        self,
        *,
        product_id: str,
        quantity: Decimal,
        note: str | None,
        admin_id: uuid.UUID,
    ) -> ManualProcurementItemResponse:
        async with self.session.begin():
            product = await self.session.scalar(
                select(Product).where(Product.id == product_id).with_for_update()
            )
            if product is None:
                raise not_found("Product")
            if product.inventory_mode != InventoryMode.TRACKED.value:
                raise DomainError(
                    409,
                    "procurement_requires_tracked_product",
                    "Only tracked products can be added to procurement",
                )
            if product.publication_status == PublicationStatus.ARCHIVED.value:
                raise DomainError(
                    409,
                    "archived_product_procurement",
                    "Archived products cannot be added to procurement",
                )
            item = await self.session.scalar(
                select(ManualProcurementItem)
                .where(ManualProcurementItem.product_id == product_id)
                .with_for_update()
            )
            if item is None:
                item = ManualProcurementItem(
                    product_id=product_id,
                    quantity=quantity,
                    note=note,
                    created_by_id=admin_id,
                )
                self.session.add(item)
            else:
                item.quantity = quantity
                item.note = note
                item.created_by_id = admin_id
        return ManualProcurementItemResponse.model_validate(item)

    async def remove_item(self, product_id: str) -> None:
        async with self.session.begin():
            item = await self.session.scalar(
                select(ManualProcurementItem)
                .where(ManualProcurementItem.product_id == product_id)
                .with_for_update()
            )
            if item is None:
                raise not_found("Manual procurement item")
            await self.session.delete(item)
