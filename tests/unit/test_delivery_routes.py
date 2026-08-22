import base64
import json
import logging
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.auth.credentials import Credentials
from pydantic import SecretStr, ValidationError

from app.enums import DeliveryRouteStatus, RouteStopStatus
from app.exceptions import DomainError
from app.main import app
from app.models import Order
from app.schemas.routes import RouteGenerateRequest, RouteNotReceivedRequest
from app.services.order_transitions import OrderTransitionService
from app.services.route_optimization import (
    GOOGLE_CLOUD_PLATFORM_SCOPE,
    GoogleRouteOptimizer,
    OptimizationStop,
    service_account_credentials_from_base64,
)
from app.services.routes import DeliveryRouteService, admin_route_detail


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


def test_service_account_credentials_are_decoded_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_info = {
        "type": "service_account",
        "project_id": "project",
        "client_email": "routes@project.iam.gserviceaccount.com",
    }
    encoded = base64.b64encode(json.dumps(credentials_info).encode()).decode()
    encoded_with_line_break = f"{encoded[:20]}\n{encoded[20:]}"
    expected = MagicMock(spec=Credentials)
    from_info = MagicMock(return_value=expected)
    monkeypatch.setattr(
        "app.services.route_optimization.service_account.Credentials.from_service_account_info",
        from_info,
    )

    credentials = service_account_credentials_from_base64(encoded_with_line_break)

    assert credentials is expected
    from_info.assert_called_once_with(
        credentials_info,
        scopes=[GOOGLE_CLOUD_PLATFORM_SCOPE],
    )


def test_invalid_service_account_credentials_return_diagnostic_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.ERROR, logger="app.services.route_optimization"):
        with pytest.raises(DomainError) as caught:
            service_account_credentials_from_base64("not-valid-base64!")

    error = caught.value
    assert error.status_code == 503
    assert error.code == "route_optimization_credentials_invalid"
    assert "Error" in error.message
    assert "Google Route Optimization credentials could not be loaded" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)


def test_delivery_route_service_passes_configured_oauth_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = SimpleNamespace(
        google_cloud_project_id="project",
        google_route_optimization_credentials_base64=SecretStr("encoded-credentials"),
        route_optimization_timeout_seconds=10,
    )
    expected = MagicMock(spec=Credentials)
    decoder = MagicMock(return_value=expected)
    monkeypatch.setattr(
        "app.services.routes.service_account_credentials_from_base64",
        decoder,
    )

    optimizer = DeliveryRouteService(MagicMock(), settings)._optimizer()

    assert isinstance(optimizer, GoogleRouteOptimizer)
    assert optimizer.credentials is expected
    decoder.assert_called_once_with("encoded-credentials")
    assert "encoded-credentials" not in repr(
        settings.google_route_optimization_credentials_base64
    )


def test_delivery_route_service_preserves_adc_fallback_without_encoded_credentials() -> None:
    settings = SimpleNamespace(
        google_cloud_project_id="project",
        google_route_optimization_credentials_base64=None,
        route_optimization_timeout_seconds=10,
    )

    optimizer = DeliveryRouteService(MagicMock(), settings)._optimizer()

    assert isinstance(optimizer, GoogleRouteOptimizer)
    assert optimizer.credentials is None


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
    expected_credentials = MagicMock(spec=Credentials)

    class FakeClient:
        def __init__(self, *, credentials: Credentials) -> None:
            assert credentials is expected_credentials

        async def optimize_tours(self, **_kwargs: object) -> object:
            return response

    monkeypatch.setattr(
        "app.services.route_optimization.routeoptimization_v1.RouteOptimizationAsyncClient",
        FakeClient,
    )
    optimized = await GoogleRouteOptimizer(
        "project", 10, credentials=expected_credentials
    ).optimize(
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


@pytest.mark.asyncio
async def test_google_adapter_exposes_and_logs_provider_failure(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingClient:
        async def optimize_tours(self, **_kwargs: object) -> object:
            raise RuntimeError("Google API permission denied")

    monkeypatch.setattr(
        "app.services.route_optimization.routeoptimization_v1.RouteOptimizationAsyncClient",
        FailingClient,
    )

    with caplog.at_level(logging.ERROR, logger="app.services.route_optimization"):
        with pytest.raises(DomainError) as caught:
            await GoogleRouteOptimizer("project", 10).optimize(
                delivery_date=date(2026, 8, 18),
                start_latitude=Decimal("31.469"),
                start_longitude=Decimal("74.272"),
                stops=[
                    OptimizationStop(
                        uuid.uuid4(),
                        Decimal("31.50"),
                        Decimal("74.30"),
                    )
                ],
            )

    error = caught.value
    assert error.status_code == 503
    assert error.code == "route_optimization_unavailable"
    assert error.message == (
        "Google Route Optimization failed (RuntimeError): Google API permission denied"
    )
    assert "Google Route Optimization request failed (RuntimeError)" in caplog.text
    assert "Google API permission denied" in caplog.text
    assert any(record.exc_info is not None for record in caplog.records)


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


def test_admin_route_detail_includes_stop_coordinates() -> None:
    now = datetime.now(UTC)
    order_id = uuid.uuid4()
    stop = SimpleNamespace(
        id=uuid.uuid4(),
        order_id=order_id,
        sequence=1,
        status=RouteStopStatus.READY.value,
        distance_from_previous_meters=1200,
        estimated_duration_seconds=360,
        completed_at=None,
        not_received_reason=None,
        outcome_note=None,
        order=SimpleNamespace(
            order_number="AB2CDE",
            customer_name_snapshot="Test Customer",
            delivery_address_snapshot="Street 1, Johar Town, Lahore",
            delivery_latitude=Decimal("31.469438"),
            delivery_longitude=Decimal("74.272647"),
        ),
    )
    route = SimpleNamespace(
        id=uuid.uuid4(),
        rider_id=uuid.uuid4(),
        rider=SimpleNamespace(name="Test Rider"),
        delivery_date=date(2026, 8, 19),
        status=DeliveryRouteStatus.GENERATED.value,
        total_distance_meters=1200,
        estimated_duration_seconds=360,
        stops=[stop],
        start_latitude=Decimal("31.460000"),
        start_longitude=Decimal("74.260000"),
        start_source="depot",
        started_at=None,
        completed_at=None,
        created_at=now,
        updated_at=now,
    )

    response = admin_route_detail(route)

    assert response.stops[0].order_id == order_id
    assert response.stops[0].latitude == Decimal("31.469438")
    assert response.stops[0].longitude == Decimal("74.272647")


@pytest.mark.asyncio
async def test_completing_stop_flushes_it_before_promoting_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_id = uuid.uuid4()
    stop_id = uuid.uuid4()
    rider_id = uuid.uuid4()
    order_id = uuid.uuid4()
    route = SimpleNamespace(
        id=route_id,
        rider_id=rider_id,
        status=DeliveryRouteStatus.IN_PROGRESS.value,
    )
    stop = SimpleNamespace(
        id=stop_id,
        order_id=order_id,
        status=RouteStopStatus.IN_PROGRESS.value,
        completed_at=None,
    )
    next_stop = SimpleNamespace(status=RouteStopStatus.PENDING.value)

    class TransactionContext:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(
            self,
            _error_type: type[BaseException] | None,
            _error: BaseException | None,
            _traceback: object,
        ) -> None:
            return None

    session = MagicMock()
    session.get = AsyncMock(return_value=None)
    session.in_transaction.return_value = False
    session.begin.return_value = TransactionContext()
    session.scalar = AsyncMock(return_value=next_stop)

    async def assert_current_stop_released(rows: list[object]) -> None:
        assert rows == [stop]
        assert stop.status == RouteStopStatus.DELIVERED.value
        assert next_stop.status == RouteStopStatus.PENDING.value

    session.flush = AsyncMock(side_effect=assert_current_stop_released)
    service = DeliveryRouteService(session, MagicMock())
    service._lock_route_stop = AsyncMock(return_value=(route, stop))  # type: ignore[method-assign]
    service._load_route = AsyncMock(return_value=route)  # type: ignore[method-assign]
    monkeypatch.setattr(
        "app.services.routes.OrderTransitionService.deliver_order_in_transaction",
        AsyncMock(),
    )
    monkeypatch.setattr("app.services.routes.rider_route_response", lambda value: value)

    await service.complete_stop(
        route_id=route_id,
        stop_id=stop_id,
        rider_id=rider_id,
        idempotency_key=uuid.uuid4(),
        delivered=True,
    )

    session.flush.assert_awaited_once_with([stop])
    assert next_stop.status == RouteStopStatus.READY.value
