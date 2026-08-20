import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Request, Response, status

from app.dependencies import SessionDep, SettingsDep
from app.schemas.orders import (
    OrderCreate,
    OrderTrackingRequest,
    PublicOrderResponse,
    PublicOrderTrackingResponse,
)
from app.services.orders import create_order, track_public_order

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("/track", response_model=PublicOrderTrackingResponse)
async def track_order(
    payload: OrderTrackingRequest, session: SessionDep
) -> PublicOrderTrackingResponse:
    return await track_public_order(session, payload)


@router.post("", response_model=PublicOrderResponse, status_code=status.HTTP_201_CREATED)
async def submit_order(
    payload: OrderCreate,
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    idempotency_key: Annotated[uuid.UUID, Header(alias="Idempotency-Key")],
) -> PublicOrderResponse:
    order, created = await create_order(
        session,
        payload,
        idempotency_key=idempotency_key,
        user_agent=request.headers.get("user-agent"),
        delivery_cutoff_hour=settings.delivery_cutoff_hour,
        delivery_default_time=settings.delivery_default_time,
    )
    if not created:
        response.status_code = status.HTTP_200_OK
    return PublicOrderResponse.model_validate(order)
