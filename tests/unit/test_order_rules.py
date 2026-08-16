import uuid
from datetime import time
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.enums import ORDER_TRANSITIONS, OrderStatus
from app.exceptions import DomainError
from app.models import Order, Rider
from app.schemas.orders import (
    CustomerInput,
    DeliveryLocationInput,
    OrderAdminUpdate,
    OrderCreate,
    OrderItemInput,
)
from app.services.delivery import DeliveryScheduleService, RiderAssignmentService
from app.services.orders import (
    DELIVERY_ORIGIN_LATITUDE,
    DELIVERY_ORIGIN_LONGITUDE,
    _assign_initial_rider,
    build_request_hash,
    calculate_delivery_charge,
    calculate_delivery_quote,
    calculate_line_total,
    normalize_pakistani_phone,
)

DELIVERY_LOCATION = DeliveryLocationInput(
    latitude=DELIVERY_ORIGIN_LATITUDE,
    longitude=DELIVERY_ORIGIN_LONGITUDE,
)


@pytest.mark.asyncio
async def test_new_order_is_assigned_to_zone_rider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rider = Rider(id=uuid.uuid4(), name="Zone rider", phone="03001112222", is_active=True)
    order = Order(
        customer_phone="+923001234567",
        status=OrderStatus.PENDING.value,
        delivery_zone_id=uuid.uuid4(),
        subtotal_pkr=0,
        delivery_charge_pkr=0,
        total_pkr=0,
    )
    select_rider = AsyncMock(return_value=rider)
    monkeypatch.setattr(RiderAssignmentService, "select_rider_for_order", select_rider)

    session = object()
    await _assign_initial_rider(session, order)  # type: ignore[arg-type]

    select_rider.assert_awaited_once_with(order)
    assert order.rider_id == rider.id


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0300-1234567", "+923001234567"),
        ("923001234567", "+923001234567"),
        ("3001234567", "+923001234567"),
    ],
)
def test_normalizes_pakistani_phone(value: str, expected: str) -> None:
    assert normalize_pakistani_phone(value) == expected


def test_rejects_invalid_phone() -> None:
    with pytest.raises(DomainError) as caught:
        normalize_pakistani_phone("12345")
    assert caught.value.code == "invalid_phone"


@pytest.mark.parametrize(
    ("price", "quantity", "expected"),
    [
        (330, Decimal("0.5"), 165),
        (101, Decimal("0.5"), 51),
        (1000, Decimal("1.250"), 1250),
    ],
)
def test_line_totals_round_half_up(price: int, quantity: Decimal, expected: int) -> None:
    assert calculate_line_total(price, quantity) == expected


def test_request_hash_normalizes_item_order() -> None:
    customer = CustomerInput(name="Ayesha", phone="03001234567", address="Lahore")
    first = OrderCreate(
        customer=customer,
        delivery_location=DELIVERY_LOCATION,
        items=[
            OrderItemInput(product_id="mango", quantity="1.000"),
            OrderItemInput(product_id="pear", quantity="0.500"),
        ],
    )
    second = OrderCreate(
        customer=customer,
        delivery_location=DELIVERY_LOCATION,
        items=[
            OrderItemInput(product_id="pear", quantity="0.500"),
            OrderItemInput(product_id="mango", quantity="1.000"),
        ],
    )
    assert build_request_hash(first, "+923001234567") == build_request_hash(second, "+923001234567")


def test_order_rejects_duplicate_products() -> None:
    with pytest.raises(ValueError, match="duplicate products"):
        OrderCreate(
            customer=CustomerInput(name="Ayesha", phone="03001234567", address="Lahore"),
            delivery_location=DELIVERY_LOCATION,
            items=[
                OrderItemInput(product_id="mango", quantity="1"),
                OrderItemInput(product_id="mango", quantity="2"),
            ],
        )


def test_refund_status_requires_amount() -> None:
    with pytest.raises(ValueError, match="refund_amount_pkr is required"):
        OrderAdminUpdate(status=OrderStatus.REFUNDED)


def test_refund_amount_is_only_allowed_with_refunded_status() -> None:
    with pytest.raises(ValueError, match="only allowed when refunding"):
        OrderAdminUpdate(status=OrderStatus.COMPLETED, refund_amount_pkr=100)


def test_refunded_update_accepts_positive_amount() -> None:
    payload = OrderAdminUpdate(status=OrderStatus.REFUNDED, refund_amount_pkr=100)
    assert payload.refund_amount_pkr == 100


def test_delivery_schedule_update_accepts_local_date_and_time() -> None:
    payload = OrderAdminUpdate(
        promised_delivery_date="2026-08-20",
        promised_delivery_time="19:30",
    )
    assert payload.promised_delivery_date.isoformat() == "2026-08-20"
    assert payload.promised_delivery_time == time(19, 30)


def test_delivery_schedule_update_rejects_null_values() -> None:
    with pytest.raises(ValueError, match="promised_delivery_time cannot be null"):
        OrderAdminUpdate(promised_delivery_time=None)


def test_delivery_schedule_uses_configured_default_time() -> None:
    schedule = DeliveryScheduleService(default_time=time(17, 45, 30))
    assert schedule.calculate_delivery_time() == time(17, 45)


def test_generic_update_rejects_operational_status() -> None:
    with pytest.raises(ValueError, match="completed"):
        OrderAdminUpdate(status=OrderStatus.CONFIRMED)


def test_completed_and_refunded_transitions() -> None:
    assert ORDER_TRANSITIONS[OrderStatus.DELIVERED] == {
        OrderStatus.COMPLETED,
        OrderStatus.REFUNDED,
    }
    assert ORDER_TRANSITIONS[OrderStatus.COMPLETED] == {OrderStatus.REFUNDED}
    assert ORDER_TRANSITIONS[OrderStatus.CANCELLED] == {OrderStatus.REFUNDED}
    assert ORDER_TRANSITIONS[OrderStatus.REFUNDED] == set()


@pytest.mark.parametrize(
    ("distance", "expected"),
    [
        ("0", 0),
        ("1", 0),
        ("1.001", 50),
        ("3", 50),
        ("3.001", 100),
        ("15.001", 350),
        ("100", 350),
    ],
)
def test_delivery_charge_tiers(distance: str, expected: int) -> None:
    assert calculate_delivery_charge(Decimal(distance)) == expected


def test_delivery_quote_at_origin_is_free() -> None:
    distance, charge = calculate_delivery_quote(
        DELIVERY_ORIGIN_LATITUDE,
        DELIVERY_ORIGIN_LONGITUDE,
    )
    assert distance == Decimal("0.000")
    assert charge == 0


def test_delivery_location_rejects_out_of_range_coordinates() -> None:
    with pytest.raises(ValueError):
        DeliveryLocationInput(latitude="91", longitude="74")


def test_request_hash_changes_with_delivery_location() -> None:
    customer = CustomerInput(name="Ayesha", phone="03001234567", address="Lahore")
    first = OrderCreate(
        customer=customer,
        delivery_location=DELIVERY_LOCATION,
        items=[OrderItemInput(product_id="mango", quantity="1")],
    )
    second = OrderCreate(
        customer=customer,
        delivery_location=DeliveryLocationInput(latitude="31.500000", longitude="74.300000"),
        items=[OrderItemInput(product_id="mango", quantity="1")],
    )
    assert build_request_hash(first, "+923001234567") != build_request_hash(second, "+923001234567")
