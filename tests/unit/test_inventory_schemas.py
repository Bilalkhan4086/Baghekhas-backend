import pytest
from pydantic import ValidationError

from app.schemas.inventory import InventoryAdjustmentCreate, PurchaseCostCreate, WasteCreate


def test_purchase_cost_type_is_an_enum() -> None:
    with pytest.raises(ValidationError):
        PurchaseCostCreate(cost_type="made_up", amount="100")


def test_waste_reason_is_an_enum() -> None:
    with pytest.raises(ValidationError):
        WasteCreate(product_id="mango", quantity="1", reason="discarded")


def test_adjustment_requires_nonzero_quantity_and_enum_reason() -> None:
    with pytest.raises(ValidationError):
        InventoryAdjustmentCreate(product_id="mango", delta="0", reason="restock")
    with pytest.raises(ValidationError):
        InventoryAdjustmentCreate(product_id="mango", delta="1", reason="not_a_reason")
