from decimal import Decimal

import pytest

from app.enums import PurchaseCostAllocationMethod
from app.exceptions import DomainError
from app.models import Purchase, PurchaseItem
from app.services.inventory import PurchaseService, _project_procurement


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
    projection = _project_procurement(
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
    projection = _project_procurement(
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
    projection = _project_procurement(
        current_stock=Decimal("7"),
        low_stock_threshold=Decimal("5"),
        confirmed_shortage=Decimal("2"),
        pending_quantity=Decimal("0"),
    )

    assert projection.projected_stock == Decimal("5")
    assert projection.order_shortage == Decimal("0")
    assert projection.low_stock_replenishment == Decimal("0")
    assert projection.suggested_purchase == Decimal("0")
