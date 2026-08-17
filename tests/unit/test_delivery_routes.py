import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.main import app
from app.models import Order
from app.schemas.routes import RouteGenerateRequest, RouteNotReceivedRequest
from app.services.order_transitions import OrderTransitionService
from app.services.route_optimization import GoogleRouteOptimizer, OptimizationStop


def test_route_generation_requires_one_start_source() -> None:
    with pytest.raises(ValidationError):
        RouteGenerateRequest()
    with pytest.raises(ValidationError):
        RouteGenerateRequest(
            latitude=Decimal("31.5"),
            longitude=Decimal("74.3"),
            use_depot_fallback=True,
        )
    assert RouteGenerateRequest(use_depot_fallback=True).use_depot_fallback


def test_other_not_received_reason_requires_note() -> None:
    with pytest.raises(ValidationError):
        RouteNotReceivedRequest(reason="other")
    parsed = RouteNotReceivedRequest(reason="other", note="Customer requested another day")
    assert parsed.note == "Customer requested another day"


def test_route_workflow_hides_direct_dispatch_action() -> None:
    order = Order(status="packing", internal_fulfillment_status="ready_for_dispatch")
    assert "dispatch" in OrderTransitionService.available_actions(order)
    assert "dispatch" not in OrderTransitionService.available_actions(
        order, route_workflow_enabled=True
    )


@pytest.mark.asyncio
async def test_google_adapter_preserves_provider_stop_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    response = SimpleNamespace(
        validation_errors=[],
        skipped_shipments=[],
        routes=[
            SimpleNamespace(
                visits=[SimpleNamespace(shipment_index=1), SimpleNamespace(shipment_index=0)],
                transitions=[
                    SimpleNamespace(
                        travel_distance_meters=1200,
                        travel_duration=SimpleNamespace(seconds=180),
                    ),
                    SimpleNamespace(
                        travel_distance_meters=800,
                        travel_duration=SimpleNamespace(seconds=120),
                    ),
                ],
            )
        ],
    )

    class FakeClient:
        async def optimize_tours(self, **_kwargs: object) -> object:
            return response

    monkeypatch.setattr(
        "app.services.route_optimization.routeoptimization_v1.RouteOptimizationAsyncClient",
        FakeClient,
    )
    optimized = await GoogleRouteOptimizer("project", 10).optimize(
        delivery_date=date(2026, 8, 18),
        start_latitude=Decimal("31.469"),
        start_longitude=Decimal("74.272"),
        stops=[
            OptimizationStop(first_id, Decimal("31.50"), Decimal("74.30")),
            OptimizationStop(second_id, Decimal("31.51"), Decimal("74.31")),
        ],
    )
    assert [leg.order_id for leg in optimized.legs] == [second_id, first_id]
    assert optimized.total_distance_meters == 2000
    assert optimized.total_duration_seconds == 300


def test_route_openapi_requires_idempotency_headers() -> None:
    paths = app.openapi()["paths"]
    operations = [
        paths["/api/v1/rider/routes/generate"]["post"],
        paths["/api/v1/rider/routes/{route_id}/start"]["post"],
        paths["/api/v1/rider/routes/{route_id}/stops/{stop_id}/delivered"]["post"],
    ]
    for operation in operations:
        headers = {
            parameter["name"]: parameter
            for parameter in operation["parameters"]
            if parameter["in"] == "header"
        }
        assert headers["Idempotency-Key"]["required"] is True
