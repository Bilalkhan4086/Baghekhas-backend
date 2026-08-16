import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from app.models import Rider
from app.routers.admin_delivery import rider_response
from app.services.delivery import RiderService


class _RiderSessionStub:
    def __init__(self) -> None:
        self.refreshed_attributes: list[str] | None = None

    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield

    def add(self, _value: object) -> None:
        return None

    async def refresh(self, rider: Rider, attribute_names: list[str]) -> None:
        self.refreshed_attributes = attribute_names
        rider.id = uuid.uuid4()
        rider.created_at = datetime.now(UTC)
        rider.rider_zones = []


@pytest.mark.asyncio
async def test_create_rider_loads_empty_zone_memberships_for_response() -> None:
    session = _RiderSessionStub()

    rider = await RiderService(session).create_rider(  # type: ignore[arg-type]
        name="Test Rider",
        phone="+923001234567",
        password="temporary-password",
        is_active=True,
    )

    assert session.refreshed_attributes == ["rider_zones"]
    assert rider_response(rider).zone_ids == []
