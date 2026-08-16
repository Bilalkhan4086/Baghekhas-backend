"""Protected zones and rider administration endpoints."""

import uuid

from fastapi import APIRouter, status

from app.dependencies import CurrentAdmin, SessionDep
from app.models import DeliveryZone, Rider
from app.schemas.delivery import (
    RiderCreate,
    RiderResponse,
    RiderUpdate,
    RiderZoneCreate,
    ZoneCreate,
    ZoneResponse,
    ZoneUpdate,
)
from app.services.delivery import DeliveryZoneService, RiderService

router = APIRouter(prefix="/admin", tags=["admin delivery"])


async def _clear_read_transaction(session: SessionDep) -> None:
    if session.in_transaction():
        await session.rollback()


def rider_response(rider: Rider) -> RiderResponse:
    return RiderResponse(
        id=rider.id,
        name=rider.name,
        phone=rider.phone,
        is_active=rider.is_active,
        created_at=rider.created_at,
        zone_ids=[membership.zone_id for membership in rider.rider_zones],
    )


@router.get("/zones", response_model=list[ZoneResponse])
async def list_zones(session: SessionDep, _admin: CurrentAdmin) -> list[DeliveryZone]:
    return await DeliveryZoneService(session).list_zones()


@router.post("/zones", response_model=ZoneResponse, status_code=status.HTTP_201_CREATED)
async def create_zone(
    payload: ZoneCreate, session: SessionDep, _admin: CurrentAdmin
) -> DeliveryZone:
    await _clear_read_transaction(session)
    return await DeliveryZoneService(session).create_zone(**payload.model_dump())


@router.patch("/zones/{zone_id}", response_model=ZoneResponse)
async def update_zone(
    zone_id: uuid.UUID, payload: ZoneUpdate, session: SessionDep, _admin: CurrentAdmin
) -> DeliveryZone:
    await _clear_read_transaction(session)
    return await DeliveryZoneService(session).update_zone(
        zone_id,
        name=payload.name,
        description=payload.description,
        boundary=payload.boundary,
        fields_set=set(payload.model_fields_set),
    )


@router.get("/riders", response_model=list[RiderResponse])
async def list_riders(session: SessionDep, _admin: CurrentAdmin) -> list[RiderResponse]:
    return [rider_response(rider) for rider in await RiderService(session).list_riders()]


@router.post("/riders", response_model=RiderResponse, status_code=status.HTTP_201_CREATED)
async def create_rider(
    payload: RiderCreate, session: SessionDep, _admin: CurrentAdmin
) -> RiderResponse:
    await _clear_read_transaction(session)
    rider = await RiderService(session).create_rider(**payload.model_dump())
    return rider_response(rider)


@router.patch("/riders/{rider_id}", response_model=RiderResponse)
async def update_rider(
    rider_id: uuid.UUID, payload: RiderUpdate, session: SessionDep, _admin: CurrentAdmin
) -> RiderResponse:
    await _clear_read_transaction(session)
    rider = await RiderService(session).update_rider(
        rider_id,
        name=payload.name,
        phone=payload.phone,
        is_active=payload.is_active,
        password=payload.password,
        fields_set=set(payload.model_fields_set),
    )
    return rider_response(rider)


@router.post("/riders/{rider_id}/zones", response_model=RiderResponse)
async def set_rider_zones(
    rider_id: uuid.UUID, payload: RiderZoneCreate, session: SessionDep, _admin: CurrentAdmin
) -> RiderResponse:
    await _clear_read_transaction(session)
    rider = await RiderService(session).set_rider_zones(rider_id, payload.zone_ids)
    return rider_response(rider)
