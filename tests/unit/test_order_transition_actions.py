import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.enums import FulfillmentStatus, OrderStatus
from app.models import Order, Rider
from app.services.delivery import RiderAssignmentService
from app.services.order_transitions import OrderTransitionService


def order(status: OrderStatus, internal: FulfillmentStatus | None = None) -> Order:
    return Order(
        customer_phone="+923001234567",
        status=status.value,
        internal_fulfillment_status=internal.value if internal else None,
        subtotal_pkr=0,
        delivery_charge_pkr=0,
        total_pkr=0,
    )


def test_available_actions_follow_the_transition_service_state_rules() -> None:
    assert OrderTransitionService.available_actions(order(OrderStatus.PENDING)) == [
        "confirm",
        "cancel",
    ]
    assert OrderTransitionService.available_actions(
        order(OrderStatus.CONFIRMED, FulfillmentStatus.STOCK_AVAILABLE)
    ) == ["cancel", "start_packing"]
    assert OrderTransitionService.available_actions(
        order(OrderStatus.PACKING, FulfillmentStatus.READY_FOR_DISPATCH)
    ) == ["dispatch", "cancel"]
    assert OrderTransitionService.available_actions(order(OrderStatus.DISPATCHED)) == [
        "deliver",
        "not_received",
    ]


class _SessionStub:
    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield

    def add(self, _value: object) -> None:
        return None


@pytest.mark.asyncio
async def test_manual_dispatch_requires_active_rider_without_zone_eligibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    selected_rider = Rider(
        id=uuid.uuid4(),
        name="Manual override",
        phone="03001112222",
        is_active=True,
    )
    packed_order = Order(
        id=order_id,
        customer_phone="+923001234567",
        status=OrderStatus.PACKING.value,
        internal_fulfillment_status=FulfillmentStatus.READY_FOR_DISPATCH.value,
        delivery_zone_id=uuid.uuid4(),
        subtotal_pkr=0,
        delivery_charge_pkr=0,
        total_pkr=0,
    )
    require_active = AsyncMock(return_value=selected_rider)
    require_eligible = AsyncMock()
    monkeypatch.setattr(RiderAssignmentService, "require_active_rider", require_active)
    monkeypatch.setattr(RiderAssignmentService, "require_eligible_rider", require_eligible)

    service = OrderTransitionService(_SessionStub())  # type: ignore[arg-type]
    service._locked_order = AsyncMock(return_value=packed_order)  # type: ignore[method-assign]

    dispatched = await service.dispatch_order(order_id, selected_rider.id, admin_id)

    require_active.assert_awaited_once_with(selected_rider.id)
    require_eligible.assert_not_awaited()
    assert dispatched.status == OrderStatus.DISPATCHED.value
    assert dispatched.rider_id == selected_rider.id


@pytest.mark.asyncio
async def test_dispatch_preserves_an_existing_active_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    assigned_rider = Rider(
        id=uuid.uuid4(),
        name="Assigned rider",
        phone="03001112222",
        is_active=True,
    )
    packed_order = Order(
        id=order_id,
        customer_phone="+923001234567",
        status=OrderStatus.PACKING.value,
        internal_fulfillment_status=FulfillmentStatus.READY_FOR_DISPATCH.value,
        rider_id=assigned_rider.id,
        subtotal_pkr=0,
        delivery_charge_pkr=0,
        total_pkr=0,
    )
    require_active = AsyncMock(return_value=assigned_rider)
    select_rider = AsyncMock()
    monkeypatch.setattr(RiderAssignmentService, "require_active_rider", require_active)
    monkeypatch.setattr(RiderAssignmentService, "select_rider_for_order", select_rider)

    service = OrderTransitionService(_SessionStub())  # type: ignore[arg-type]
    service._locked_order = AsyncMock(return_value=packed_order)  # type: ignore[method-assign]

    dispatched = await service.dispatch_order(order_id, None, admin_id)

    require_active.assert_awaited_once_with(assigned_rider.id)
    select_rider.assert_not_awaited()
    assert dispatched.rider_id == assigned_rider.id


@pytest.mark.asyncio
async def test_admin_reassignment_accepts_active_rider_outside_order_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_id = uuid.uuid4()
    admin_id = uuid.uuid4()
    current_rider_id = uuid.uuid4()
    selected_rider = Rider(
        id=uuid.uuid4(),
        name="Admin override",
        phone="03009998888",
        is_active=True,
    )
    pending_order = Order(
        id=order_id,
        customer_phone="+923001234567",
        status=OrderStatus.PENDING.value,
        delivery_zone_id=uuid.uuid4(),
        rider_id=current_rider_id,
        subtotal_pkr=0,
        delivery_charge_pkr=0,
        total_pkr=0,
    )
    session = _SessionStub()
    session.scalar = AsyncMock(return_value=pending_order)  # type: ignore[attr-defined]
    require_active = AsyncMock(return_value=selected_rider)
    require_eligible = AsyncMock()
    monkeypatch.setattr(RiderAssignmentService, "require_active_rider", require_active)
    monkeypatch.setattr(RiderAssignmentService, "require_eligible_rider", require_eligible)

    reassigned = await RiderAssignmentService(session).reassign_rider(  # type: ignore[arg-type]
        order_id,
        selected_rider.id,
        admin_id,
    )

    require_active.assert_awaited_once_with(selected_rider.id)
    require_eligible.assert_not_awaited()
    assert reassigned.rider_id == selected_rider.id
