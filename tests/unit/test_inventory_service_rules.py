import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.enums import PurchaseCostAllocationMethod
from app.exceptions import DomainError
from app.models import InventoryBatch, InventoryReservation, Purchase, PurchaseItem
from app.services import inventory as inventory_module
from app.services.inventory import PurchaseService, ReservationService, _FifoAllocation
from app.services.procurement import project_procurement


def purchase(method: PurchaseCostAllocationMethod, overhead: str) -> Purchase:
    return Purchase(
        purchase_number="PUR-TEST",
        supplier="Mandi supplier",
        purchase_date="2026-08-01",
        subtotal=Decimal("400"),
        additional_cost=Decimal(overhead),
        total_cost=Decimal("400") + Decimal(overhead),
        cost_allocation_method=method.value,
        status="draft",
    )


def items() -> list[PurchaseItem]:
    return [
        PurchaseItem(
            quantity=Decimal("2"),
            unit_cost=Decimal("100"),
            line_cost=Decimal("200"),
        ),
        PurchaseItem(
            quantity=Decimal("1"),
            unit_cost=Decimal("200"),
            line_cost=Decimal("200"),
        ),
    ]


def test_purchase_overhead_allocation_by_weight_uses_decimal_remainder() -> None:
    purchase_items = items()

    PurchaseService._allocate_overhead(
        object(),
        purchase(PurchaseCostAllocationMethod.BY_WEIGHT, "60"),
        purchase_items,
    )

    assert purchase_items[0].manual_overhead == Decimal("40")
    assert purchase_items[1].manual_overhead == Decimal("20")


def test_purchase_overhead_allocation_by_value_and_manual_validation() -> None:
    purchase_items = items()
    PurchaseService._allocate_overhead(
        object(),
        purchase(PurchaseCostAllocationMethod.BY_PURCHASE_VALUE, "60"),
        purchase_items,
    )
    assert [item.manual_overhead for item in purchase_items] == [Decimal("30"), Decimal("30")]

    manual_items = items()
    manual_items[0].manual_overhead = Decimal("20")
    manual_items[1].manual_overhead = Decimal("30")
    with pytest.raises(DomainError, match="Manual overhead allocations"):
        PurchaseService._allocate_overhead(
            object(),
            purchase(PurchaseCostAllocationMethod.MANUAL, "60"),
            manual_items,
        )


def test_procurement_projection_covers_order_shortage_and_low_stock_buffer() -> None:
    projection = project_procurement(
        current_stock=Decimal("3"),
        low_stock_threshold=Decimal("5"),
        confirmed_shortage=Decimal("0"),
        pending_quantity=Decimal("5"),
    )

    assert projection.projected_stock == Decimal("0")
    assert projection.order_shortage == Decimal("2")
    assert projection.low_stock_replenishment == Decimal("5")
    assert projection.suggested_purchase == Decimal("7")


def test_procurement_projection_replenishes_stock_consumed_by_pending_orders() -> None:
    projection = project_procurement(
        current_stock=Decimal("7"),
        low_stock_threshold=Decimal("5"),
        confirmed_shortage=Decimal("0"),
        pending_quantity=Decimal("3"),
    )

    assert projection.projected_stock == Decimal("4")
    assert projection.order_shortage == Decimal("0")
    assert projection.low_stock_replenishment == Decimal("1")
    assert projection.suggested_purchase == Decimal("1")


def test_procurement_projection_uses_new_stock_before_recommending_another_purchase() -> None:
    projection = project_procurement(
        current_stock=Decimal("7"),
        low_stock_threshold=Decimal("5"),
        confirmed_shortage=Decimal("2"),
        pending_quantity=Decimal("0"),
    )

    assert projection.projected_stock == Decimal("5")
    assert projection.order_shortage == Decimal("0")
    assert projection.low_stock_replenishment == Decimal("0")
    assert projection.suggested_purchase == Decimal("0")


@pytest.mark.asyncio
async def test_stock_sync_flushes_pending_batches_before_calculating_total() -> None:
    class NoAutoflushSession:
        flushed = False

        async def flush(self) -> None:
            self.flushed = True

        async def scalar(self, _statement: object) -> Decimal:
            # With production's autoflush=False, the aggregate can see pending batch
            # changes only after the service explicitly flushes them.
            assert self.flushed
            return Decimal("8.000")

    session = NoAutoflushSession()
    product = SimpleNamespace(id="mango", stock_quantity=Decimal("0"))

    quantity = await inventory_module._sync_available_quantity(  # type: ignore[arg-type]
        session, product
    )

    assert quantity == Decimal("8.000")
    assert product.stock_quantity == Decimal("8.000")


@pytest.mark.asyncio
async def test_reservations_preallocate_ids_without_intermediate_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch = InventoryBatch(
        id=uuid.uuid4(),
        product_id="mango",
        received_quantity=Decimal("3"),
        remaining_quantity=Decimal("1"),
        unit_cost=Decimal("100"),
        effective_cost=Decimal("100"),
        received_at=datetime.now(UTC),
    )
    allocate_fifo = AsyncMock(
        return_value=(
            object(),
            [_FifoAllocation(batch=batch, quantity=Decimal("2"))],
            Decimal("0"),
        )
    )
    monkeypatch.setattr(inventory_module, "_allocate_fifo", allocate_fifo)

    added: list[object] = []

    class AddOnlySession:
        def add(self, value: object) -> None:
            added.append(value)

    result = await ReservationService(AddOnlySession()).reserve_stock_in_transaction(  # type: ignore[arg-type]
        product_id="mango",
        quantity=Decimal("2"),
        order_id=uuid.uuid4(),
    )

    reservation = next(value for value in added if isinstance(value, InventoryReservation))
    assert result.reservation_ids == [reservation.id]
    assert isinstance(reservation.id, uuid.UUID)
