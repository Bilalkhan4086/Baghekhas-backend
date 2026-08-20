"""Transactional inventory-domain services.

Only this module mutates inventory batches or appends batch-aware movement rows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums import (
    InventoryMovementType,
    InventoryReason,
    InventoryReservationStatus,
    PurchaseCostAllocationMethod,
    PurchaseStatus,
    WasteReason,
)
from app.exceptions import DomainError, not_found
from app.models import (
    InventoryBatch,
    InventoryMovement,
    InventoryReservation,
    Order,
    Product,
    Purchase,
    PurchaseCost,
    PurchaseItem,
    WasteRecord,
)
from app.schemas.common import Page
from app.schemas.inventory import (
    InventoryBatchResponse,
    InventoryMovementDetailResponse,
    InventorySummaryResponse,
    ProcurementRequirementResponse,
    PurchaseCostResponse,
    PurchaseItemResponse,
    PurchasePage,
    PurchaseResponse,
    WastePage,
    WasteRecordResponse,
)
from app.services.procurement import ProcurementReadService

ZERO = Decimal("0")


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return getattr(diagnostic, "constraint_name", None)


@dataclass(frozen=True)
class PurchaseItemDraft:
    product_id: str
    quantity: Decimal
    unit_cost: Decimal
    manual_overhead: Decimal | None = None


@dataclass(frozen=True)
class PurchaseCostDraft:
    cost_type: str
    amount: Decimal
    notes: str | None = None


@dataclass(frozen=True)
class InventorySummary:
    physical: Decimal
    reserved: Decimal
    available: Decimal
    incoming: Decimal


@dataclass(frozen=True)
class ReservationResult:
    reservation_ids: list[uuid.UUID]
    reserved_quantity: Decimal
    shortage_quantity: Decimal


@dataclass(frozen=True)
class WasteResult:
    records: list[WasteRecord]
    cost: Decimal


@dataclass(frozen=True)
class AdjustmentResult:
    movements: list[InventoryMovement]
    quantity_delta: Decimal


@dataclass(frozen=True)
class _FifoAllocation:
    batch: InventoryBatch
    quantity: Decimal


def _require_positive(quantity: Decimal, field: str = "Quantity") -> None:
    if quantity <= ZERO:
        raise DomainError(422, "invalid_quantity", f"{field} must be greater than zero")


async def _locked_product(session: AsyncSession, product_id: str) -> Product:
    product = await session.scalar(
        select(Product).where(Product.id == product_id).with_for_update()
    )
    if product is None:
        raise not_found("Product")
    return product


async def _sync_available_quantity(session: AsyncSession, product: Product) -> Decimal:
    """Synchronize the legacy aggregate with free batch stock in the current transaction."""
    # Production sessions disable autoflush. Persist pending batch inserts and quantity
    # changes before summing them, otherwise stock_quantity reflects the previous database
    # state until the next inventory mutation.
    await session.flush()
    available = await session.scalar(
        select(func.coalesce(func.sum(InventoryBatch.remaining_quantity), ZERO)).where(
            InventoryBatch.product_id == product.id
        )
    )
    product.stock_quantity = Decimal(available or ZERO)
    return product.stock_quantity


async def _materialize_legacy_balance(session: AsyncSession, product: Product) -> None:
    """Create one zero-cost batch for a pre-M3 aggregate balance when needed.

    This runs only within an inventory mutation transaction. The M3 migration performs
    the normal backfill; this defensive path preserves the legacy admin service's
    behavior for a product created directly by an older caller or a test fixture.
    """
    if product.stock_quantity <= ZERO:
        return
    existing_batch = await session.scalar(
        select(InventoryBatch.id).where(InventoryBatch.product_id == product.id).limit(1)
    )
    if existing_batch is not None:
        return
    session.add(
        InventoryBatch(
            product_id=product.id,
            received_quantity=product.stock_quantity,
            remaining_quantity=product.stock_quantity,
            unit_cost=ZERO,
            effective_cost=ZERO,
            received_at=datetime.now(UTC),
        )
    )
    await session.flush()


async def _allocate_fifo(
    session: AsyncSession, product_id: str, requested: Decimal
) -> tuple[Product, list[_FifoAllocation], Decimal]:
    """Lock and decrement FIFO batches atomically within the caller's transaction."""
    _require_positive(requested)
    product = await _locked_product(session, product_id)
    await _materialize_legacy_balance(session, product)
    batches = list(
        (
            await session.scalars(
                select(InventoryBatch)
                .where(
                    InventoryBatch.product_id == product_id,
                    InventoryBatch.remaining_quantity > ZERO,
                )
                .order_by(InventoryBatch.received_at.asc(), InventoryBatch.id.asc())
                .with_for_update()
            )
        ).all()
    )
    remaining = requested
    allocations: list[_FifoAllocation] = []
    for batch in batches:
        if remaining <= ZERO:
            break
        allocated = min(batch.remaining_quantity, remaining)
        batch.remaining_quantity -= allocated
        allocations.append(_FifoAllocation(batch=batch, quantity=allocated))
        remaining -= allocated
    await _sync_available_quantity(session, product)
    return product, allocations, remaining


def _movement(
    *,
    product_id: str,
    batch: InventoryBatch,
    delta: Decimal,
    movement_type: InventoryMovementType,
    reason: InventoryReason,
    reference_type: str,
    reference_id: uuid.UUID,
    actor_admin_id: uuid.UUID | None,
    note: str | None = None,
    reference_order_id: uuid.UUID | None = None,
) -> InventoryMovement:
    return InventoryMovement(
        product_id=product_id,
        batch_id=batch.id,
        delta=delta,
        resulting_quantity=batch.remaining_quantity,
        reason=reason.value,
        note=note,
        reference_order_id=reference_order_id,
        actor_admin_id=actor_admin_id,
        movement_type=movement_type.value,
        reference_type=reference_type,
        reference_id=reference_id,
    )


class PurchaseService:
    """Owns purchase draft lifecycle and receipt in one transaction per mutation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _purchase_values(
        supplier: str,
        items: list[PurchaseItemDraft],
        additional_costs: list[PurchaseCostDraft],
    ) -> tuple[str, Decimal, Decimal, Decimal]:
        if not items:
            raise DomainError(
                422,
                "purchase_items_required",
                "A purchase needs at least one item",
            )
        normalized_supplier = supplier.strip()
        if not normalized_supplier:
            raise DomainError(422, "supplier_required", "A supplier is required")
        for item in items:
            _require_positive(item.quantity)
            if item.unit_cost < ZERO:
                raise DomainError(422, "invalid_cost", "Unit cost cannot be negative")
        subtotal = sum((item.quantity * item.unit_cost for item in items), ZERO)
        additional_cost = sum((cost.amount for cost in additional_costs), ZERO)
        if additional_cost < ZERO:
            raise DomainError(422, "invalid_cost", "Additional cost cannot be negative")
        return normalized_supplier, subtotal, additional_cost, subtotal + additional_cost

    async def _require_products(self, items: list[PurchaseItemDraft]) -> None:
        requested_product_ids = {item.product_id for item in items}
        existing_product_ids = set(
            (
                await self.session.scalars(
                    select(Product.id).where(Product.id.in_(requested_product_ids))
                )
            ).all()
        )
        missing_product_ids = sorted(requested_product_ids - existing_product_ids)
        if missing_product_ids:
            raise DomainError(
                422,
                "invalid_purchase_product",
                f"Unknown purchase product: {missing_product_ids[0]}",
            )

    async def _draft_has_batches(self, purchase_id: uuid.UUID) -> bool:
        batch_count = await self.session.scalar(
            select(func.count())
            .select_from(InventoryBatch)
            .join(PurchaseItem, PurchaseItem.id == InventoryBatch.purchase_item_id)
            .where(PurchaseItem.purchase_id == purchase_id)
        )
        return bool(batch_count)

    async def create_purchase(
        self,
        *,
        supplier: str,
        purchase_date: date,
        items: list[PurchaseItemDraft],
        additional_costs: list[PurchaseCostDraft],
        created_by_id: uuid.UUID,
        cost_allocation_method: PurchaseCostAllocationMethod,
        notes: str | None = None,
    ) -> Purchase:
        """Atomically persist a draft purchase; inventory is not changed on success."""
        supplier, subtotal, additional_cost, total_cost = self._purchase_values(
            supplier, items, additional_costs
        )
        purchase = Purchase(
            purchase_number=f"PUR-{uuid.uuid4().hex[:12].upper()}",
            supplier=supplier,
            purchase_date=purchase_date,
            notes=notes.strip() if notes else None,
            subtotal=subtotal,
            additional_cost=additional_cost,
            total_cost=total_cost,
            cost_allocation_method=cost_allocation_method.value,
            status=PurchaseStatus.DRAFT.value,
            created_by_id=created_by_id,
            items=[
                PurchaseItem(
                    product_id=item.product_id,
                    quantity=item.quantity,
                    unit_cost=item.unit_cost,
                    line_cost=item.quantity * item.unit_cost,
                    manual_overhead=item.manual_overhead,
                )
                for item in items
            ],
            costs=[
                PurchaseCost(
                    cost_type=cost.cost_type,
                    amount=cost.amount,
                    notes=cost.notes,
                )
                for cost in additional_costs
            ],
        )
        try:
            async with self.session.begin():
                await self._require_products(items)
                self.session.add(purchase)
        except IntegrityError as error:
            await self.session.rollback()
            constraint = _constraint_name(error)
            if constraint == "purchases_purchase_number_key":
                raise DomainError(
                    409,
                    "purchase_conflict",
                    "The generated purchase number already exists; retry the request",
                ) from error
            if constraint == "purchase_items_product_id_fkey":
                raise DomainError(
                    422,
                    "invalid_purchase_product",
                    "A selected purchase product is unavailable",
                ) from error
            if constraint == "purchases_created_by_fkey":
                raise DomainError(
                    409,
                    "administrator_unavailable",
                    "The administrator account is unavailable",
                ) from error
            raise
        return purchase

    async def update_purchase(
        self,
        purchase_id: uuid.UUID,
        *,
        supplier: str,
        purchase_date: date,
        items: list[PurchaseItemDraft],
        additional_costs: list[PurchaseCostDraft],
        cost_allocation_method: PurchaseCostAllocationMethod,
        notes: str | None = None,
    ) -> Purchase:
        """Fully replace an untouched draft purchase without changing inventory."""
        supplier, subtotal, additional_cost, total_cost = self._purchase_values(
            supplier, items, additional_costs
        )
        try:
            async with self.session.begin():
                purchase = await self.session.scalar(
                    select(Purchase).where(Purchase.id == purchase_id).with_for_update()
                )
                if purchase is None:
                    raise not_found("Purchase")
                if purchase.status != PurchaseStatus.DRAFT.value:
                    raise DomainError(
                        409,
                        "purchase_not_editable",
                        "Only draft purchases can be edited",
                    )
                if await self._draft_has_batches(purchase_id):
                    raise DomainError(
                        409,
                        "purchase_has_inventory",
                        "A purchase linked to inventory cannot be edited",
                    )
                await self._require_products(items)
                await self.session.execute(
                    delete(PurchaseCost).where(PurchaseCost.purchase_id == purchase_id)
                )
                await self.session.execute(
                    delete(PurchaseItem).where(PurchaseItem.purchase_id == purchase_id)
                )
                purchase.supplier = supplier
                purchase.purchase_date = purchase_date
                purchase.notes = notes.strip() if notes else None
                purchase.subtotal = subtotal
                purchase.additional_cost = additional_cost
                purchase.total_cost = total_cost
                purchase.cost_allocation_method = cost_allocation_method.value
                self.session.add_all(
                    [
                        PurchaseItem(
                            purchase_id=purchase_id,
                            product_id=item.product_id,
                            quantity=item.quantity,
                            unit_cost=item.unit_cost,
                            line_cost=item.quantity * item.unit_cost,
                            manual_overhead=item.manual_overhead,
                        )
                        for item in items
                    ]
                    + [
                        PurchaseCost(
                            purchase_id=purchase_id,
                            cost_type=cost.cost_type,
                            amount=cost.amount,
                            notes=cost.notes,
                        )
                        for cost in additional_costs
                    ]
                )
        except IntegrityError as error:
            await self.session.rollback()
            constraint = _constraint_name(error)
            if constraint == "purchase_items_product_id_fkey":
                raise DomainError(
                    422,
                    "invalid_purchase_product",
                    "A selected purchase product is unavailable",
                ) from error
            raise
        return purchase

    async def delete_purchase(self, purchase_id: uuid.UUID) -> None:
        """Permanently delete an untouched draft and its non-inventory child rows."""
        try:
            async with self.session.begin():
                purchase = await self.session.scalar(
                    select(Purchase).where(Purchase.id == purchase_id).with_for_update()
                )
                if purchase is None:
                    raise not_found("Purchase")
                if purchase.status != PurchaseStatus.DRAFT.value:
                    raise DomainError(
                        409,
                        "purchase_not_deletable",
                        "Only draft purchases can be deleted",
                    )
                if await self._draft_has_batches(purchase_id):
                    raise DomainError(
                        409,
                        "purchase_has_inventory",
                        "A purchase linked to inventory cannot be deleted",
                    )
                await self.session.execute(
                    delete(PurchaseCost).where(PurchaseCost.purchase_id == purchase_id)
                )
                await self.session.execute(
                    delete(PurchaseItem).where(PurchaseItem.purchase_id == purchase_id)
                )
                await self.session.delete(purchase)
        except IntegrityError as error:
            await self.session.rollback()
            raise DomainError(
                409,
                "purchase_delete_conflict",
                "The purchase is referenced by inventory and cannot be deleted",
            ) from error

    async def receive_purchase(self, purchase_id: uuid.UUID) -> Purchase:
        """Atomically receive a draft purchase and create batches/movements.

        A retry after a successful receipt is a no-op and returns the received purchase.
        """
        async with self.session.begin():
            purchase = await self.session.scalar(
                select(Purchase).where(Purchase.id == purchase_id).with_for_update()
            )
            if purchase is None:
                raise not_found("Purchase")
            if purchase.status == PurchaseStatus.RECEIVED.value:
                return purchase
            if purchase.status != PurchaseStatus.DRAFT.value:
                raise DomainError(
                    409,
                    "purchase_not_receivable",
                    "Only draft purchases can be received",
                )
            items = list(
                (
                    await self.session.scalars(
                        select(PurchaseItem)
                        .where(PurchaseItem.purchase_id == purchase_id)
                        .order_by(PurchaseItem.id)
                    )
                ).all()
            )
            self._allocate_overhead(purchase, items)
            receipt_time = datetime.combine(purchase.purchase_date, time.min, tzinfo=UTC)
            products: dict[str, Product] = {}
            for item in items:
                product = products.get(item.product_id)
                if product is None:
                    product = await _locked_product(self.session, item.product_id)
                    products[item.product_id] = product
                overhead = item.manual_overhead or ZERO
                effective_cost = (item.line_cost + overhead) / item.quantity
                batch = InventoryBatch(
                    id=uuid.uuid4(),
                    product_id=item.product_id,
                    purchase_item_id=item.id,
                    received_quantity=item.quantity,
                    remaining_quantity=item.quantity,
                    unit_cost=item.unit_cost,
                    effective_cost=effective_cost,
                    received_at=receipt_time,
                )
                self.session.add(batch)
                self.session.add(
                    _movement(
                        product_id=item.product_id,
                        batch=batch,
                        delta=item.quantity,
                        movement_type=InventoryMovementType.PURCHASE,
                        reason=InventoryReason.RESTOCK,
                        reference_type="purchase",
                        reference_id=purchase.id,
                        actor_admin_id=purchase.created_by_id,
                    )
                )
            purchase.status = PurchaseStatus.RECEIVED.value
            for product_id, product in products.items():
                await _sync_available_quantity(self.session, product)
                await InventoryService(self.session).recheck_procurement_requirements(product_id)
        return purchase

    def _allocate_overhead(self, purchase: Purchase, items: list[PurchaseItem]) -> None:
        """Assign overhead with Decimal arithmetic before receipt in the same transaction."""
        overhead = purchase.additional_cost
        method = PurchaseCostAllocationMethod(purchase.cost_allocation_method)
        if method == PurchaseCostAllocationMethod.MANUAL:
            total = sum((item.manual_overhead or ZERO for item in items), ZERO)
            if total != overhead:
                raise DomainError(
                    422,
                    "manual_allocation_mismatch",
                    "Manual overhead allocations must equal the additional cost",
                )
            return
        denominator = (
            sum((item.quantity for item in items), ZERO)
            if method == PurchaseCostAllocationMethod.BY_WEIGHT
            else purchase.subtotal
        )
        if denominator <= ZERO and overhead > ZERO:
            raise DomainError(
                422,
                "invalid_allocation",
                "Cannot allocate overhead across zero value",
            )
        assigned = ZERO
        for item in items[:-1]:
            weight = (
                item.quantity
                if method == PurchaseCostAllocationMethod.BY_WEIGHT
                else item.line_cost
            )
            item.manual_overhead = overhead * weight / denominator if denominator else ZERO
            assigned += item.manual_overhead
        if items:
            items[-1].manual_overhead = overhead - assigned

    async def cancel_purchase(self, purchase_id: uuid.UUID) -> Purchase:
        """Atomically transition a draft purchase to cancelled without inventory mutation."""
        async with self.session.begin():
            purchase = await self.session.scalar(
                select(Purchase).where(Purchase.id == purchase_id).with_for_update()
            )
            if purchase is None:
                raise not_found("Purchase")
            if purchase.status != PurchaseStatus.DRAFT.value:
                raise DomainError(
                    409,
                    "purchase_not_cancellable",
                    "Only draft purchases can be cancelled",
                )
            purchase.status = PurchaseStatus.CANCELLED.value
        return purchase


class InventoryReadService:
    """Read models needed by the protected inventory operations screens."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def purchase_detail(self, purchase_id: uuid.UUID) -> PurchaseResponse:
        purchase = await self.session.scalar(
            select(Purchase)
            .where(Purchase.id == purchase_id)
            .options(selectinload(Purchase.items), selectinload(Purchase.costs))
            .execution_options(populate_existing=True)
        )
        if purchase is None:
            raise not_found("Purchase")
        batches = list(
            (
                await self.session.scalars(
                    select(InventoryBatch)
                    .join(PurchaseItem, PurchaseItem.id == InventoryBatch.purchase_item_id)
                    .where(PurchaseItem.purchase_id == purchase.id)
                    .order_by(InventoryBatch.received_at.asc(), InventoryBatch.id.asc())
                )
            ).all()
        )
        return self._purchase_response(purchase, batches)

    async def list_purchases(
        self,
        *,
        page: int,
        page_size: int,
        status: PurchaseStatus | None,
        date_from: date | None,
        date_to: date | None,
        supplier: str | None,
    ) -> PurchasePage:
        filters = []
        if status is not None:
            filters.append(Purchase.status == status.value)
        if date_from is not None:
            filters.append(Purchase.purchase_date >= date_from)
        if date_to is not None:
            filters.append(Purchase.purchase_date <= date_to)
        if supplier:
            filters.append(Purchase.supplier.ilike(f"%{supplier.strip()}%"))
        total = await self.session.scalar(
            select(func.count()).select_from(Purchase).where(*filters)
        )
        purchases = list(
            (
                await self.session.scalars(
                    select(Purchase)
                    .where(*filters)
                    .options(selectinload(Purchase.items), selectinload(Purchase.costs))
                    .order_by(Purchase.purchase_date.desc(), Purchase.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return PurchasePage(
            items=[self._purchase_response(purchase, []) for purchase in purchases],
            page=page,
            page_size=page_size,
            total=total or 0,
        )

    async def inventory_summary(self, product_id: str) -> InventorySummaryResponse:
        summary = await InventoryService(self.session).get_inventory_summary(product_id)
        return InventorySummaryResponse(product_id=product_id, **summary.__dict__)

    async def batches(self, product_id: str) -> list[InventoryBatchResponse]:
        if await self.session.get(Product, product_id) is None:
            raise not_found("Product")
        batches = (
            await self.session.scalars(
                select(InventoryBatch)
                .where(InventoryBatch.product_id == product_id)
                .order_by(InventoryBatch.received_at.asc(), InventoryBatch.id.asc())
            )
        ).all()
        return [InventoryBatchResponse.model_validate(batch) for batch in batches]

    async def movements(
        self,
        product_id: str,
        *,
        page: int,
        page_size: int,
        movement_type: InventoryMovementType | None,
        date_from: date | None,
        date_to: date | None,
    ) -> Page[InventoryMovementDetailResponse]:
        if await self.session.get(Product, product_id) is None:
            raise not_found("Product")
        filters = [InventoryMovement.product_id == product_id]
        if movement_type is not None:
            filters.append(InventoryMovement.movement_type == movement_type.value)
        if date_from is not None:
            filters.append(func.date(InventoryMovement.created_at) >= date_from)
        if date_to is not None:
            filters.append(func.date(InventoryMovement.created_at) <= date_to)
        total = await self.session.scalar(
            select(func.count()).select_from(InventoryMovement).where(*filters)
        )
        movements = (
            await self.session.scalars(
                select(InventoryMovement)
                .where(*filters)
                .order_by(InventoryMovement.created_at.desc(), InventoryMovement.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return Page(
            items=[
                InventoryMovementDetailResponse.model_validate(movement)
                for movement in movements
            ],
            page=page,
            page_size=page_size,
            total=total or 0,
        )

    async def waste_records(self, *, page: int, page_size: int) -> WastePage:
        total = await self.session.scalar(select(func.count()).select_from(WasteRecord))
        records = (
            await self.session.scalars(
                select(WasteRecord)
                .order_by(WasteRecord.created_at.desc(), WasteRecord.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
        return WastePage(
            items=[WasteRecordResponse.model_validate(record) for record in records],
            page=page,
            page_size=page_size,
            total=total or 0,
        )

    async def procurement_requirements(self) -> list[ProcurementRequirementResponse]:
        return await ProcurementReadService(self.session).requirements()

    @staticmethod
    def _purchase_response(
        purchase: Purchase, batches: list[InventoryBatch]
    ) -> PurchaseResponse:
        return PurchaseResponse(
            id=purchase.id,
            purchase_number=purchase.purchase_number,
            supplier=purchase.supplier,
            purchase_date=purchase.purchase_date,
            notes=purchase.notes,
            subtotal=purchase.subtotal,
            additional_cost=purchase.additional_cost,
            total_cost=purchase.total_cost,
            cost_allocation_method=purchase.cost_allocation_method,
            status=purchase.status,
            created_by_id=purchase.created_by_id,
            created_at=purchase.created_at,
            updated_at=purchase.updated_at,
            items=[PurchaseItemResponse.model_validate(item) for item in purchase.items],
            costs=[PurchaseCostResponse.model_validate(cost) for cost in purchase.costs],
            batches=[InventoryBatchResponse.model_validate(batch) for batch in batches],
        )


class InventoryService:
    """Read inventory summaries from batch/reservation state without mutation."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_inventory_summary(self, product_id: str) -> InventorySummary:
        """Read physical, reserved, available, and incoming stock in one aggregate query."""
        if await self.session.get(Product, product_id) is None:
            raise not_found("Product")
        free = (
            select(func.coalesce(func.sum(InventoryBatch.remaining_quantity), ZERO))
            .where(InventoryBatch.product_id == product_id)
            .scalar_subquery()
        )
        reserved = (
            select(func.coalesce(func.sum(InventoryReservation.quantity), ZERO))
            .where(
                InventoryReservation.product_id == product_id,
                InventoryReservation.status == InventoryReservationStatus.ACTIVE.value,
            )
            .scalar_subquery()
        )
        incoming = (
            select(func.coalesce(func.sum(PurchaseItem.quantity), ZERO))
            .join(Purchase, Purchase.id == PurchaseItem.purchase_id)
            .where(
                PurchaseItem.product_id == product_id,
                Purchase.status == PurchaseStatus.DRAFT.value,
            )
            .scalar_subquery()
        )
        free_stock, reserved_stock, incoming_stock = (
            await self.session.execute(select(free, reserved, incoming))
        ).one()
        free_decimal = Decimal(free_stock or ZERO)
        reserved_decimal = Decimal(reserved_stock or ZERO)
        return InventorySummary(
            physical=free_decimal + reserved_decimal,
            reserved=reserved_decimal,
            available=free_decimal,
            incoming=Decimal(incoming_stock or ZERO),
        )

    async def recheck_procurement_requirements(self, product_id: str) -> InventorySummary:
        """Return a current stock snapshot for the future procurement-rule caller.

        This is the explicit post-receipt hook consumed by Milestone 4. It deliberately
        creates no purchase order or order-side state because procurement policy belongs
        to that later service layer.
        """
        return await self.get_inventory_summary(product_id)

    async def get_physical_stock(self, product_id: str) -> Decimal:
        """Read on-hand stock, including quantities currently held by active reservations."""
        return (await self.get_inventory_summary(product_id)).physical

    async def get_reserved_stock(self, product_id: str) -> Decimal:
        """Read active reservation quantity without mutation."""
        return (await self.get_inventory_summary(product_id)).reserved

    async def get_available_stock(self, product_id: str) -> Decimal:
        """Read the single-source free-to-allocate quantity without mutation."""
        return (await self.get_inventory_summary(product_id)).available

    async def get_incoming_stock(self, product_id: str) -> Decimal:
        """Read draft-purchase quantity only; procurement orders are not incoming stock."""
        return (await self.get_inventory_summary(product_id)).incoming

    async def calculate_inventory_cost(self, product_id: str) -> Decimal:
        """Read weighted-average remaining-batch cost; this method never writes."""
        numerator = await self.session.scalar(
            select(
                func.coalesce(
                    func.sum(InventoryBatch.remaining_quantity * InventoryBatch.effective_cost),
                    ZERO,
                )
            ).where(InventoryBatch.product_id == product_id)
        )
        denominator = await self.session.scalar(
            select(func.coalesce(func.sum(InventoryBatch.remaining_quantity), ZERO)).where(
                InventoryBatch.product_id == product_id
            )
        )
        quantity = Decimal(denominator or ZERO)
        return ZERO if quantity == ZERO else Decimal(numerator or ZERO) / quantity


class InventoryLifecycleService:
    """Owns opening-balance and tracking-mode inventory mutations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_opening_balance(
        self,
        *,
        product_id: str,
        quantity: Decimal,
        admin_id: uuid.UUID,
        note: str,
    ) -> InventoryMovement | None:
        """Atomically materialize a non-negative opening balance as a zero-cost batch.

        A zero opening balance only synchronizes the aggregate product quantity and does
        not create a meaningless zero-quantity batch or movement.
        """
        async with self.session.begin():
            return await self.record_opening_balance_in_transaction(
                product_id=product_id,
                quantity=quantity,
                admin_id=admin_id,
                note=note,
            )

    async def record_opening_balance_in_transaction(
        self,
        *,
        product_id: str,
        quantity: Decimal,
        admin_id: uuid.UUID,
        note: str,
    ) -> InventoryMovement | None:
        """Record an opening balance inside the caller's active transaction."""
        if quantity < ZERO:
            raise DomainError(422, "invalid_quantity", "Opening balance cannot be negative")
        product = await _locked_product(self.session, product_id)
        existing_batch = await self.session.scalar(
            select(InventoryBatch.id).where(InventoryBatch.product_id == product_id).limit(1)
        )
        if existing_batch is not None:
            raise DomainError(
                409,
                "opening_balance_exists",
                "Opening balance requires a product without inventory batches",
            )
        if quantity == ZERO:
            product.stock_quantity = ZERO
            return None
        batch = InventoryBatch(
            id=uuid.uuid4(),
            product_id=product_id,
            received_quantity=quantity,
            remaining_quantity=quantity,
            unit_cost=ZERO,
            effective_cost=ZERO,
            received_at=datetime.now(UTC),
        )
        self.session.add(batch)
        movement = _movement(
            product_id=product_id,
            batch=batch,
            delta=quantity,
            movement_type=InventoryMovementType.ADJUSTMENT_IN,
            reason=InventoryReason.OPENING_BALANCE,
            reference_type="opening_balance",
            reference_id=uuid.uuid4(),
            actor_admin_id=admin_id,
            note=note,
        )
        self.session.add(movement)
        await _sync_available_quantity(self.session, product)
        return movement

    async def set_tracking_mode(
        self,
        *,
        product_id: str,
        tracked: bool,
        opening_quantity: Decimal | None,
        manual_available: bool | None,
        admin_id: uuid.UUID,
        note: str | None,
    ) -> Product:
        """Atomically enable tracking with an opening batch or disable it without a movement."""
        async with self.session.begin():
            product = await _locked_product(self.session, product_id)
            target_mode = "tracked" if tracked else "untracked"
            if product.inventory_mode == target_mode:
                raise DomainError(
                    409,
                    "inventory_mode_unchanged",
                    "Product already uses this inventory mode",
                )
            if not tracked:
                if manual_available is None:
                    raise DomainError(
                        422,
                        "manual_availability_required",
                        "Manual availability is required when disabling tracking",
                    )
                product.inventory_mode = target_mode
                product.manual_available = manual_available
                return product
            if opening_quantity is None or opening_quantity < ZERO:
                raise DomainError(
                    422,
                    "opening_quantity_required",
                    "A non-negative opening quantity is required when enabling tracking",
                )
            existing_batch = await self.session.scalar(
                select(InventoryBatch.id).where(InventoryBatch.product_id == product_id).limit(1)
            )
            if existing_batch is not None:
                raise DomainError(
                    409,
                    "inventory_batches_exist",
                    "Use an inventory adjustment before re-enabling tracking",
                )
            product.inventory_mode = target_mode
            product.stock_quantity = ZERO
            if opening_quantity == ZERO:
                return product
            batch = InventoryBatch(
                id=uuid.uuid4(),
                product_id=product_id,
                received_quantity=opening_quantity,
                remaining_quantity=opening_quantity,
                unit_cost=ZERO,
                effective_cost=ZERO,
                received_at=datetime.now(UTC),
            )
            self.session.add(batch)
            self.session.add(
                _movement(
                    product_id=product_id,
                    batch=batch,
                    delta=opening_quantity,
                    movement_type=InventoryMovementType.ADJUSTMENT_IN,
                    reason=InventoryReason.OPENING_BALANCE,
                    reference_type="opening_balance",
                    reference_id=uuid.uuid4(),
                    actor_admin_id=admin_id,
                    note=note or "Inventory tracking enabled",
                )
            )
            await _sync_available_quantity(self.session, product)
        return product


class ReservationService:
    """Owns FIFO reservation, release, and sale transitions in atomic transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve_stock(
        self, *, product_id: str, quantity: Decimal, order_id: uuid.UUID
    ) -> ReservationResult:
        """Atomically reserve FIFO stock and return a partial-shortage result when needed."""
        async with self.session.begin():
            return await self.reserve_stock_in_transaction(
                product_id=product_id, quantity=quantity, order_id=order_id
            )

    async def reserve_stock_in_transaction(
        self, *, product_id: str, quantity: Decimal, order_id: uuid.UUID
    ) -> ReservationResult:
        """Reserve FIFO stock inside the caller's already-open atomic transaction."""
        _require_positive(quantity)
        group_id = uuid.uuid4()
        _, allocations, shortage = await _allocate_fifo(self.session, product_id, quantity)
        reservation_ids: list[uuid.UUID] = []
        for allocation in allocations:
            reservation = InventoryReservation(
                id=uuid.uuid4(),
                order_id=order_id,
                product_id=product_id,
                batch_id=allocation.batch.id,
                allocation_group_id=group_id,
                quantity=allocation.quantity,
                status=InventoryReservationStatus.ACTIVE.value,
            )
            self.session.add(reservation)
            reservation_ids.append(reservation.id)
            self.session.add(
                _movement(
                    product_id=product_id,
                    batch=allocation.batch,
                    delta=-allocation.quantity,
                    movement_type=InventoryMovementType.RESERVATION,
                    reason=InventoryReason.ORDER_FULFILLMENT,
                    reference_type="order",
                    reference_id=order_id,
                    actor_admin_id=None,
                )
            )
        return ReservationResult(reservation_ids, quantity - shortage, shortage)

    async def release_reservation(self, reservation_id: uuid.UUID) -> bool:
        """Atomically release one active reservation; returns false for an idempotent retry."""
        async with self.session.begin():
            return await self.release_reservation_in_transaction(reservation_id)

    async def release_reservation_in_transaction(self, reservation_id: uuid.UUID) -> bool:
        """Release one reservation inside the caller's already-open atomic transaction."""
        reservation = await self.session.scalar(
            select(InventoryReservation)
            .where(InventoryReservation.id == reservation_id)
            .with_for_update()
        )
        if reservation is None:
            raise not_found("Inventory reservation")
        if reservation.status != InventoryReservationStatus.ACTIVE.value:
            return False
        product = await _locked_product(self.session, reservation.product_id)
        batch = await self.session.scalar(
            select(InventoryBatch)
            .where(InventoryBatch.id == reservation.batch_id)
            .with_for_update()
        )
        if batch is None:
            raise DomainError(
                409,
                "reservation_batch_missing",
                "Reservation batch is unavailable",
            )
        batch.remaining_quantity += reservation.quantity
        reservation.status = InventoryReservationStatus.RELEASED.value
        reservation.released_at = datetime.now(UTC)
        self.session.add(
            _movement(
                product_id=reservation.product_id,
                batch=batch,
                delta=reservation.quantity,
                movement_type=InventoryMovementType.RESERVATION_RELEASE,
                reason=InventoryReason.RETURN,
                reference_type="order",
                reference_id=reservation.order_id,
                actor_admin_id=None,
            )
        )
        await _sync_available_quantity(self.session, product)
        return True

    async def record_sale(self, reservation_id: uuid.UUID) -> Decimal | None:
        """Atomically consume one reservation and append one sale movement; retries are no-ops."""
        async with self.session.begin():
            return await self.record_sale_in_transaction(reservation_id)

    async def record_sale_in_transaction(self, reservation_id: uuid.UUID) -> Decimal | None:
        """Consume one reservation inside the caller's already-open atomic transaction."""
        reservation = await self.session.scalar(
            select(InventoryReservation)
            .where(InventoryReservation.id == reservation_id)
            .with_for_update()
        )
        if reservation is None:
            raise not_found("Inventory reservation")
        if reservation.status != InventoryReservationStatus.ACTIVE.value:
            return None
        batch = await self.session.get(InventoryBatch, reservation.batch_id)
        if batch is None:
            raise DomainError(
                409,
                "reservation_batch_missing",
                "Reservation batch is unavailable",
            )
        reservation.status = InventoryReservationStatus.CONSUMED.value
        reservation.consumed_at = datetime.now(UTC)
        self.session.add(
            _movement(
                product_id=reservation.product_id,
                batch=batch,
                delta=-reservation.quantity,
                movement_type=InventoryMovementType.SALE,
                reason=InventoryReason.ORDER_FULFILLMENT,
                reference_type="order",
                reference_id=reservation.order_id,
                actor_admin_id=None,
            )
        )
        return reservation.quantity * batch.effective_cost


class CostingService:
    """Calculates inventory valuation and FIFO reservation COGS without writes."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def calculate_cogs_for_reservation(self, reservation_id: uuid.UUID) -> Decimal:
        """Read total FIFO COGS for the reservation's allocation group without mutation."""
        reservation = await self.session.get(InventoryReservation, reservation_id)
        if reservation is None:
            raise not_found("Inventory reservation")
        filters = [InventoryReservation.id == reservation.id]
        if reservation.allocation_group_id is not None:
            filters = [InventoryReservation.allocation_group_id == reservation.allocation_group_id]
        total = await self.session.scalar(
            select(
                func.coalesce(
                    func.sum(InventoryReservation.quantity * InventoryBatch.effective_cost),
                    ZERO,
                )
            )
            .join(InventoryBatch, InventoryBatch.id == InventoryReservation.batch_id)
            .where(*filters)
        )
        return Decimal(total or ZERO)

    async def suggest_selling_price(self, product_id: str, target_margin_pct: Decimal) -> Decimal:
        """Read a suggested price from weighted inventory cost; no product field is changed."""
        if target_margin_pct < ZERO or target_margin_pct >= Decimal("100"):
            raise DomainError(
                422,
                "invalid_margin",
                "Target margin must be from 0 up to 100",
            )
        cost = await InventoryService(self.session).calculate_inventory_cost(product_id)
        return ZERO if cost == ZERO else cost / (Decimal("1") - target_margin_pct / Decimal("100"))


class WasteService:
    """Owns FIFO waste and stock-correction mutations in one transaction each."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record_waste(
        self,
        *,
        product_id: str,
        quantity: Decimal,
        reason: WasteReason,
        notes: str | None,
        admin_id: uuid.UUID,
    ) -> WasteResult:
        """Atomically remove FIFO stock, append movements, and persist allocated waste cost."""
        _require_positive(quantity)
        async with self.session.begin():
            _, allocations, shortage = await _allocate_fifo(self.session, product_id, quantity)
            if shortage > ZERO:
                raise DomainError(
                    409,
                    "insufficient_stock",
                    "Waste quantity exceeds physical stock",
                )
            records: list[WasteRecord] = []
            movement_reason = (
                InventoryReason.DAMAGE
                if reason == WasteReason.DAMAGED
                else InventoryReason.SPOILAGE
            )
            movement_type = (
                InventoryMovementType.DAMAGE
                if reason == WasteReason.DAMAGED
                else InventoryMovementType.WASTE
            )
            for allocation in allocations:
                record = WasteRecord(
                    id=uuid.uuid4(),
                    product_id=product_id,
                    batch_id=allocation.batch.id,
                    quantity=allocation.quantity,
                    reason=reason.value,
                    notes=notes.strip() if notes else None,
                    cost=allocation.quantity * allocation.batch.effective_cost,
                    admin_id=admin_id,
                )
                self.session.add(record)
                records.append(record)
                self.session.add(
                    _movement(
                        product_id=product_id,
                        batch=allocation.batch,
                        delta=-allocation.quantity,
                        movement_type=movement_type,
                        reason=movement_reason,
                        reference_type="waste",
                        reference_id=record.id,
                        actor_admin_id=admin_id,
                        note=record.notes,
                    )
                )
        return WasteResult(
            records=records,
            cost=sum((record.cost for record in records), ZERO),
        )

    async def record_adjustment(
        self,
        *,
        product_id: str,
        quantity_delta: Decimal,
        reason: str,
        notes: str | None,
        admin_id: uuid.UUID,
        inventory_reason: InventoryReason = InventoryReason.CORRECTION,
        reference_order_id: uuid.UUID | None = None,
    ) -> AdjustmentResult:
        """Atomically apply a reasoned zero-cost correction or legacy adjustment.

        Positive deltas create one zero-cost batch. Negative deltas use the shared FIFO
        allocator. If an optional order reference is supplied it is validated and copied
        to every movement in this transaction.
        """
        if quantity_delta == ZERO:
            raise DomainError(422, "invalid_quantity", "Adjustment quantity cannot be zero")
        if not reason.strip():
            raise DomainError(422, "adjustment_reason_required", "Adjustment reason is required")
        async with self.session.begin():
            if reference_order_id is not None:
                referenced_order = await self.session.get(Order, reference_order_id)
                if referenced_order is None:
                    raise not_found("Referenced order")
            reference_type = "order" if reference_order_id is not None else "adjustment"
            reference_id = reference_order_id or uuid.uuid4()
            if quantity_delta > ZERO:
                product = await _locked_product(self.session, product_id)
                await _materialize_legacy_balance(self.session, product)
                batch = InventoryBatch(
                    id=uuid.uuid4(),
                    product_id=product_id,
                    received_quantity=quantity_delta,
                    remaining_quantity=quantity_delta,
                    unit_cost=ZERO,
                    effective_cost=ZERO,
                    received_at=datetime.now(UTC),
                )
                self.session.add(batch)
                movement = _movement(
                    product_id=product_id,
                    batch=batch,
                    delta=quantity_delta,
                    movement_type=InventoryMovementType.ADJUSTMENT_IN,
                    reason=inventory_reason,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    actor_admin_id=admin_id,
                    note=f"{reason.strip()}: {notes.strip()}" if notes else reason.strip(),
                    reference_order_id=reference_order_id,
                )
                self.session.add(movement)
                await _sync_available_quantity(self.session, product)
                return AdjustmentResult(
                    movements=[movement],
                    quantity_delta=quantity_delta,
                )
            _, allocations, shortage = await _allocate_fifo(
                self.session,
                product_id,
                -quantity_delta,
            )
            if shortage > ZERO:
                raise DomainError(
                    409,
                    "insufficient_stock",
                    "Adjustment exceeds physical stock",
                )
            movements = [
                _movement(
                    product_id=product_id,
                    batch=allocation.batch,
                    delta=-allocation.quantity,
                    movement_type=InventoryMovementType.ADJUSTMENT_OUT,
                    reason=inventory_reason,
                    reference_type=reference_type,
                    reference_id=reference_id,
                    actor_admin_id=admin_id,
                    note=f"{reason.strip()}: {notes.strip()}" if notes else reason.strip(),
                    reference_order_id=reference_order_id,
                )
                for allocation in allocations
            ]
            self.session.add_all(movements)
        return AdjustmentResult(movements=movements, quantity_delta=quantity_delta)
