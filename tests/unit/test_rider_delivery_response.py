import uuid

from app.models import Customer, Order
from app.routers.rider import _customer_area, _detail_response


def test_rider_list_area_excludes_street_address() -> None:
    assert _customer_area("House 10, Street 2, Johar Town, Lahore") == "Johar Town"


def test_rider_list_area_uses_safe_fallback_for_unstructured_address() -> None:
    assert _customer_area("House 10 Street 2") == "Open delivery for address"


def test_rider_list_area_does_not_treat_a_two_part_street_address_as_an_area() -> None:
    assert _customer_area("House 10, Lahore") == "Open delivery for address"


def test_rider_detail_uses_order_delivery_snapshot() -> None:
    order = Order(
        id=uuid.uuid4(),
        customer_phone="+923001234567",
        customer_name_snapshot="Original Name",
        delivery_address_snapshot="Original Address",
        status="dispatched",
        subtotal_pkr=100,
        delivery_charge_pkr=0,
        total_pkr=100,
        items=[],
    )
    order.customer = Customer(
        phone=order.customer_phone,
        name="New Name",
        address="New Address",
    )
    response = _detail_response(order)
    assert response.customer_name == "Original Name"
    assert response.delivery_address == "Original Address"
    assert response.customer_phone == order.customer_phone
