from decimal import Decimal

import pytest

from app.enums import PurchaseCostAllocationMethod
from app.exceptions import DomainError
from app.models import Purchase, PurchaseItem
from app.services.inventory import PurchaseService


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
