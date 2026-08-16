import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.enums import InventoryMode, PublicationStatus, StockPolicy
from app.models import AdminUser, Product
from app.schemas.products import (
    InventoryAdjustmentCreate,
    InventoryMovementResponse,
    InventoryTrackingUpdate,
    ProductCreate,
    ProductUpdate,
)
from app.services.inventory import InventoryLifecycleService
from app.services.orders import requires_current_stock
from app.services.products import create_product


def make_product(**overrides: object) -> Product:
    values: dict[str, object] = {
        "id": "mango",
        "name": "Mango",
        "description": "Fresh mangoes",
        "image_url": "/mango.png",
        "category": "fruit",
        "catalog_type": "product",
        "unit_label": "kg",
        "base_price_pkr": 500,
        "pricing_type": "fixed",
        "publication_status": "active",
        "inventory_mode": "untracked",
        "manual_available": True,
        "stock_quantity": Decimal("0"),
        "low_stock_threshold": Decimal("0"),
    }
    values.update(overrides)
    return Product(**values)


def test_untracked_product_uses_manual_availability() -> None:
    assert make_product(manual_available=True).available is True
    assert make_product(manual_available=False).available is False


def test_tracked_product_uses_stock() -> None:
    assert make_product(inventory_mode="tracked", stock_quantity=Decimal("0.001")).available
    assert not make_product(inventory_mode="tracked", stock_quantity=Decimal("0")).available


def test_arrange_on_demand_is_customer_orderable_without_tracked_stock() -> None:
    product = make_product(
        inventory_mode=InventoryMode.TRACKED.value,
        stock_quantity=Decimal("0"),
        stock_policy=StockPolicy.ARRANGE_ON_DEMAND.value,
    )
    assert product.available is True
    assert product.customer_availability == "available_on_demand"
    assert requires_current_stock(product) is False


def test_in_stock_only_product_requires_current_stock() -> None:
    product = make_product(
        inventory_mode=InventoryMode.TRACKED.value,
        stock_quantity=Decimal("0"),
        stock_policy=StockPolicy.IN_STOCK_ONLY.value,
    )
    assert requires_current_stock(product) is True


def test_non_active_products_are_unavailable() -> None:
    assert not make_product(publication_status=PublicationStatus.COMING_SOON.value).available
    assert not make_product(publication_status=PublicationStatus.ARCHIVED.value).available


def test_low_stock_applies_only_to_tracked_products() -> None:
    tracked = make_product(
        inventory_mode="tracked",
        stock_quantity=Decimal("1.5"),
        low_stock_threshold=Decimal("2"),
    )
    untracked = make_product(
        inventory_mode="untracked",
        stock_quantity=Decimal("1.5"),
        low_stock_threshold=Decimal("2"),
    )
    assert tracked.low_stock is True
    assert untracked.low_stock is False


class _ProductSessionStub:
    def __init__(self) -> None:
        self.refreshed = None

    def in_transaction(self) -> bool:
        return False

    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield

    def add(self, _value: object) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def refresh(self, value: object) -> None:
        self.refreshed = value


@pytest.mark.asyncio
async def test_create_product_preserves_decimal_threshold_and_refreshes_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opening_balance = AsyncMock(return_value=None)
    monkeypatch.setattr(
        InventoryLifecycleService,
        "record_opening_balance_in_transaction",
        opening_balance,
    )
    payload = ProductCreate(
        id="seasonal-mango",
        name="Seasonal Mango",
        description="Fresh mangoes",
        image_url="https://example.com/mango.webp",
        category="Seasonal",
        catalog_type="product",
        unit_label="kg",
        base_price_pkr=500,
        inventory_mode="tracked",
        opening_stock="0.000",
        low_stock_threshold="5.000",
    )
    actor = AdminUser(
        id=uuid.uuid4(),
        email="admin@example.com",
        password_hash="unused",
    )
    session = _ProductSessionStub()

    created = await create_product(session, payload, actor)  # type: ignore[arg-type]

    assert created.low_stock_threshold == Decimal("5.000")
    assert isinstance(created.low_stock_threshold, Decimal)
    assert created.low_stock is True
    assert session.refreshed is created


def test_quantity_precision_is_limited_to_three_decimals() -> None:
    with pytest.raises(ValidationError, match="3 decimal places"):
        InventoryAdjustmentCreate(delta="0.0001", reason="restock")


def test_inventory_adjustment_cannot_be_zero() -> None:
    with pytest.raises(ValidationError, match="cannot be zero"):
        InventoryAdjustmentCreate(delta="0", reason="restock")


def test_tracking_mode_requires_opening_balance() -> None:
    with pytest.raises(ValidationError, match="opening_quantity"):
        InventoryTrackingUpdate(mode=InventoryMode.TRACKED)


def test_product_update_rejects_null_for_required_database_field() -> None:
    with pytest.raises(ValidationError, match="Fields cannot be null: name"):
        ProductUpdate(name=None)


def test_legacy_movement_response_accepts_system_actor() -> None:
    response = InventoryMovementResponse(
        id="00000000-0000-0000-0000-000000000001",
        product_id="mango",
        delta="-1",
        resulting_quantity="0",
        reason="order_fulfillment",
        note=None,
        reference_order_id=None,
        actor_admin_id=None,
        created_at="2026-08-14T00:00:00Z",
    )
    assert response.actor_admin_id is None
