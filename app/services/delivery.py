"""Delivery scheduling, GeoJSON-zone, and rider-assignment services."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.exceptions import DomainError, not_found
from app.models import DeliveryZone, Order, OrderStatusHistory, Rider, RiderZone
from app.security import password_hash

KARACHI = ZoneInfo("Asia/Karachi")


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return cast(str | None, getattr(diagnostic, "constraint_name", None))


def _inside_ring(longitude: float, latitude: float, ring: list[list[float]]) -> bool:
    inside = False
    previous = ring[-1]
    for current in ring:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > latitude) != (y2 > latitude) and longitude < (x2 - x1) * (latitude - y1) / (
            y2 - y1
        ) + x1:
            inside = not inside
        previous = current
    return inside


def _outer_ring(boundary: dict[str, object]) -> list[list[float]] | None:
    """Return a validated outer Polygon ring, skipping malformed persisted boundaries."""
    if boundary.get("type") != "Polygon":
        return None
    coordinates = boundary.get("coordinates")
    if not isinstance(coordinates, list) or not coordinates:
        return None
    ring = coordinates[0]
    if not isinstance(ring, list) or len(ring) < 4:
        return None
    normalized: list[list[float]] = []
    for position in ring:
        if not isinstance(position, list) or len(position) < 2:
            return None
        longitude, latitude = position[:2]
        if (
            not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
            or not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
        ):
            return None
        normalized.append([float(longitude), float(latitude)])
    if normalized[0] != normalized[-1]:
        return None
    return normalized


class DeliveryScheduleService:
    """Calculates immutable delivery promises in Asia/Karachi."""

    def __init__(self, cutoff_hour: int = 15, default_time: time = time(18, 0)) -> None:
        self.cutoff_hour = cutoff_hour
        self.default_time = default_time.replace(second=0, microsecond=0, tzinfo=None)

    def calculate_delivery_date(self, order_created_at: datetime) -> date:
        """Return same-day before cutoff, next-day at/after cutoff, in Karachi time."""
        local = (
            order_created_at.astimezone(KARACHI)
            if order_created_at.tzinfo
            else order_created_at.replace(tzinfo=UTC).astimezone(KARACHI)
        )
        return local.date() if local.hour < self.cutoff_hour else local.date() + timedelta(days=1)

    def calculate_delivery_time(self) -> time:
        """Return the configured Karachi wall-clock delivery time."""
        return self.default_time

    def get_available_delivery_dates(
        self, _zone_id: uuid.UUID, now: datetime | None = None
    ) -> list[date]:
        """Return the next two preview dates without persisting an order promise."""
        first = self.calculate_delivery_date(now or datetime.now(UTC))
        return [first, first + timedelta(days=1)]


class DeliveryZoneService:
    """Owns GeoJSON zone resolution and zone CRUD transactions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def resolve_zone(self, latitude: float, longitude: float) -> DeliveryZone | None:
        """Read the first deterministic GeoJSON polygon zone containing the coordinate."""
        zones = list(
            (await self.session.scalars(select(DeliveryZone).order_by(DeliveryZone.id))).all()
        )
        for zone in zones:
            boundary = zone.boundary or {}
            ring = _outer_ring(boundary)
            if ring is not None and _inside_ring(longitude, latitude, ring):
                return zone
        return None

    async def list_zones(self) -> list[DeliveryZone]:
        zones = await self.session.scalars(select(DeliveryZone).order_by(DeliveryZone.name))
        return list(zones.all())

    async def create_zone(
        self, *, name: str, description: str | None, boundary: dict[str, object] | None
    ) -> DeliveryZone:
        try:
            async with self.session.begin():
                zone = DeliveryZone(name=name, description=description, boundary=boundary)
                self.session.add(zone)
        except IntegrityError as error:
            if _constraint_name(error) == "delivery_zones_name_key":
                raise DomainError(
                    409, "zone_exists", "A delivery zone with this name exists"
                ) from error
            raise
        return zone

    async def update_zone(
        self,
        zone_id: uuid.UUID,
        *,
        name: str | None,
        description: str | None,
        boundary: dict[str, object] | None,
        fields_set: set[str],
    ) -> DeliveryZone:
        try:
            async with self.session.begin():
                zone = await self.session.scalar(
                    select(DeliveryZone).where(DeliveryZone.id == zone_id).with_for_update()
                )
                if zone is None:
                    raise not_found("Delivery zone")
                if "name" in fields_set and name is not None:
                    zone.name = name
                if "description" in fields_set:
                    zone.description = description
                if "boundary" in fields_set:
                    zone.boundary = boundary
        except IntegrityError as error:
            if _constraint_name(error) == "delivery_zones_name_key":
                raise DomainError(
                    409, "zone_exists", "A delivery zone with this name exists"
                ) from error
            raise
        return zone


class RiderService:
    """Owns protected rider records and their delivery-zone memberships."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_riders(self) -> list[Rider]:
        return list(
            (
                await self.session.scalars(
                    select(Rider)
                    .options(selectinload(Rider.rider_zones))
                    .order_by(Rider.name, Rider.id)
                )
            ).all()
        )

    async def create_rider(self, *, name: str, phone: str, password: str, is_active: bool) -> Rider:
        try:
            async with self.session.begin():
                rider = Rider(
                    name=name.strip(),
                    phone=phone.strip(),
                    password_hash=password_hash.hash(password),
                    is_active=is_active,
                )
                self.session.add(rider)
        except IntegrityError as error:
            if _constraint_name(error) == "riders_phone_key":
                raise DomainError(
                    409, "rider_phone_exists", "A rider with this phone exists"
                ) from error
            raise
        await self.session.refresh(rider, attribute_names=["rider_zones"])
        return rider

    async def update_rider(
        self,
        rider_id: uuid.UUID,
        *,
        name: str | None,
        phone: str | None,
        is_active: bool | None,
        password: str | None,
        fields_set: set[str],
    ) -> Rider:
        try:
            async with self.session.begin():
                rider = await self.session.scalar(
                    select(Rider)
                    .where(Rider.id == rider_id)
                    .options(selectinload(Rider.rider_zones))
                    .with_for_update()
                )
                if rider is None:
                    raise not_found("Rider")
                if "name" in fields_set and name is not None:
                    rider.name = name.strip()
                if "phone" in fields_set and phone is not None:
                    rider.phone = phone.strip()
                if "is_active" in fields_set and is_active is not None:
                    rider.is_active = is_active
                    rider.auth_version += 1
                if "password" in fields_set and password is not None:
                    rider.password_hash = password_hash.hash(password)
                    rider.auth_version += 1
        except IntegrityError as error:
            if _constraint_name(error) == "riders_phone_key":
                raise DomainError(
                    409, "rider_phone_exists", "A rider with this phone exists"
                ) from error
            raise
        return rider

    async def set_rider_zones(self, rider_id: uuid.UUID, zone_ids: list[uuid.UUID]) -> Rider:
        unique_zone_ids = list(dict.fromkeys(zone_ids))
        async with self.session.begin():
            rider = await self.session.scalar(
                select(Rider).where(Rider.id == rider_id).with_for_update()
            )
            if rider is None:
                raise not_found("Rider")
            zones = list(
                (
                    await self.session.scalars(
                        select(DeliveryZone).where(DeliveryZone.id.in_(unique_zone_ids))
                    )
                ).all()
            )
            if len(zones) != len(unique_zone_ids):
                raise DomainError(422, "invalid_zone", "One or more delivery zones do not exist")

            # Execute the delete before staging replacements. ORM collection replacement can
            # flush inserts first and violate the unique key when a requested zone is retained.
            await self.session.execute(
                delete(RiderZone).where(RiderZone.rider_id == rider.id)
            )
            self.session.add_all(
                RiderZone(rider_id=rider.id, zone_id=zone_id)
                for zone_id in unique_zone_ids
            )
            await self.session.flush()
            await self.session.refresh(rider, attribute_names=["rider_zones"])
        return rider


class RiderAssignmentService:
    """Assigns active zone riders by scheduled workload for the promised delivery date."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assign_rider(self, order_id: uuid.UUID) -> Rider | None:
        """Return and persist the deterministic least-loaded eligible rider atomically."""
        async with self.session.begin():
            order = await self.session.scalar(
                select(Order).where(Order.id == order_id).with_for_update()
            )
            if order is None:
                raise not_found("Order")
            rider = await self.select_rider_for_order(order)
            if rider is not None:
                order.rider_id = rider.id
            return rider

    async def select_rider_for_order(self, order: Order) -> Rider | None:
        """Select an eligible rider without opening or committing a transaction."""
        if order.delivery_zone_id is None:
            return None
        riders = list(
            (
                await self.session.scalars(
                    select(Rider)
                    .join(RiderZone)
                    .where(
                        RiderZone.zone_id == order.delivery_zone_id,
                        Rider.is_active.is_(True),
                    )
                    .order_by(Rider.id)
                    .with_for_update(of=Rider)
                )
            ).all()
        )
        if not riders:
            return None
        assignment_date = order.promised_delivery_date or datetime.now(KARACHI).date()
        loads = await self.get_rider_daily_loads(
            [rider.id for rider in riders],
            assignment_date,
        )
        return min(riders, key=lambda candidate: (loads[candidate.id], candidate.id))

    async def require_active_rider(self, rider_id: uuid.UUID) -> Rider:
        """Require an existing active rider for an explicit administrator assignment."""
        rider = await self.session.get(Rider, rider_id)
        if rider is None or not rider.is_active:
            raise DomainError(422, "invalid_rider", "An active rider is required")
        return rider

    async def require_eligible_rider(self, order: Order, rider_id: uuid.UUID) -> Rider:
        """Require an active rider assigned to the order's zone when it has one."""
        rider = await self.require_active_rider(rider_id)
        if order.delivery_zone_id is not None:
            membership = await self.session.scalar(
                select(RiderZone.id).where(
                    RiderZone.rider_id == rider.id,
                    RiderZone.zone_id == order.delivery_zone_id,
                )
            )
            if membership is None:
                raise DomainError(
                    409,
                    "rider_zone_mismatch",
                    "The rider is not assigned to this delivery zone",
                )
        return rider

    async def get_rider_daily_load(self, rider_id: uuid.UUID, on_date: date) -> int:
        """Read non-cancelled orders assigned to a rider for one promised date."""
        loads = await self.get_rider_daily_loads([rider_id], on_date)
        return loads[rider_id]

    async def get_rider_daily_loads(
        self,
        rider_ids: list[uuid.UUID],
        on_date: date,
    ) -> dict[uuid.UUID, int]:
        """Read scheduled daily loads for eligible riders in one aggregate query."""
        rows = (
            await self.session.execute(
                select(Order.rider_id, func.count(Order.id))
                .where(
                    Order.rider_id.in_(rider_ids),
                    Order.promised_delivery_date == on_date,
                    Order.status != "cancelled",
                )
                .group_by(Order.rider_id)
            )
        ).all()
        loads: dict[uuid.UUID, int] = {
            rider_id: int(load) for rider_id, load in rows if rider_id is not None
        }
        return {rider_id: int(loads.get(rider_id, 0)) for rider_id in rider_ids}

    async def reassign_rider(
        self, order_id: uuid.UUID, new_rider_id: uuid.UUID, admin_id: uuid.UUID
    ) -> Order:
        """Atomically apply an explicit admin rider override and append an audit row."""
        async with self.session.begin():
            order = await self.session.scalar(
                select(Order).where(Order.id == order_id).with_for_update()
            )
            if order is None:
                raise not_found("Order")
            if order.status in {
                "delivered",
                "not_received",
                "completed",
                "cancelled",
                "refunded",
            }:
                raise DomainError(
                    409, "invalid_rider_assignment", "Rider assignment is not allowed"
                )
            rider = await self.require_active_rider(new_rider_id)
            previous_rider_id = order.rider_id
            order.rider_id = rider.id
            self.session.add(
                OrderStatusHistory(
                    order_id=order.id,
                    from_status=order.status,
                    to_status=order.status,
                    note=(
                        "Rider assigned by administrator"
                        if previous_rider_id is None
                        else "Rider reassigned by administrator"
                    ),
                    actor_admin_id=admin_id,
                )
            )
        return order
