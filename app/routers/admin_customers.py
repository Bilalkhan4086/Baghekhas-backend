"""Protected customer relationship management endpoints."""

from fastapi import APIRouter, Query

from app.dependencies import CurrentAdmin, SessionDep
from app.enums import CustomerSegment
from app.exceptions import not_found
from app.models import Customer
from app.schemas.common import Page
from app.schemas.customers import (
    CustomerAddressResponse,
    CustomerDetailResponse,
    CustomerListItemResponse,
    CustomerUpdate,
)
from app.services.customers import customer_detail, list_customers

router = APIRouter(prefix="/admin/customers", tags=["admin customers"])


@router.get("", response_model=Page[CustomerListItemResponse])
async def customers(
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="spend", pattern="^(spend|frequency|recent|name)$"),
    segment: CustomerSegment | None = None,
) -> Page[CustomerListItemResponse]:
    return await list_customers(
        session,
        page=page,
        page_size=page_size,
        query=q,
        sort=sort,
        segment=segment,
    )


@router.get("/{customer_phone}/addresses", response_model=list[CustomerAddressResponse])
async def customer_addresses(
    customer_phone: str, session: SessionDep, _admin: CurrentAdmin
) -> list[CustomerAddressResponse]:
    return (await customer_detail(session, customer_phone)).addresses


@router.get("/{customer_phone}", response_model=CustomerDetailResponse)
async def get_customer(
    customer_phone: str, session: SessionDep, _admin: CurrentAdmin
) -> CustomerDetailResponse:
    return await customer_detail(session, customer_phone)


@router.patch("/{customer_phone}", response_model=CustomerDetailResponse)
async def update_customer(
    customer_phone: str,
    payload: CustomerUpdate,
    session: SessionDep,
    _admin: CurrentAdmin,
) -> CustomerDetailResponse:
    customer = await session.get(Customer, customer_phone)
    if customer is None:
        raise not_found("Customer")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(customer, field, value)
    await session.commit()
    return await customer_detail(session, customer_phone)
