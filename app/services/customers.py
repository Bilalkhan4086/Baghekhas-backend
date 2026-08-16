"""Admin customer search and aggregate reporting queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.enums import CustomerSegment, OrderStatus
from app.exceptions import not_found
from app.models import Customer, Order, OrderItem
from app.schemas.common import Page
from app.schemas.customers import (
    CustomerDetailResponse,
    CustomerListItemResponse,
    CustomerOrderSummaryResponse,
    FavoriteItemResponse,
)

REALIZED_ORDER_STATUSES = (OrderStatus.DELIVERED.value, OrderStatus.COMPLETED.value)


def _ninety_days_ago() -> datetime:
    return datetime.now(UTC) - timedelta(days=90)


def _aggregate_columns() -> tuple[Any, Any, Any]:
    realized = Order.status.in_(REALIZED_ORDER_STATUSES)
    spend = func.coalesce(func.sum(case((realized, Order.total_pkr), else_=0)), 0).label(
        "lifetime_spend_pkr"
    )
    frequency = func.coalesce(
        func.sum(case((realized & (Order.created_at >= _ninety_days_ago()), 1), else_=0)), 0
    ).label("order_frequency_90d")
    last_order = func.max(Order.created_at).label("last_order_at")
    return spend, frequency, last_order


async def list_customers(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    query: str | None,
    sort: str,
    segment: CustomerSegment | None = None,
) -> Page[CustomerListItemResponse]:
    spend, frequency, last_order = _aggregate_columns()
    filters = []
    if query:
        term = f"%{query.strip()}%"
        filters.append(or_(Customer.name.ilike(term), Customer.phone.ilike(term)))
    statement = (
        select(Customer, spend, frequency, last_order)
        .outerjoin(Order, Order.customer_phone == Customer.phone)
        .where(*filters)
        .group_by(Customer.phone)
    )
    if segment is CustomerSegment.RECENT:
        statement = statement.having(frequency > 0)
    elif segment is CustomerSegment.INACTIVE:
        statement = statement.having(frequency == 0)
    elif segment is CustomerSegment.HIGH_VALUE:
        statement = statement.having(spend > 0)

    total = await session.scalar(select(func.count()).select_from(statement.subquery()))
    if sort == "frequency":
        statement = statement.order_by(desc(frequency), Customer.name, Customer.phone)
    elif sort == "recent":
        statement = statement.order_by(desc(last_order).nullslast(), Customer.name, Customer.phone)
    elif sort == "name":
        statement = statement.order_by(Customer.name, Customer.phone)
    else:
        statement = statement.order_by(desc(spend), Customer.name, Customer.phone)
    rows = (await session.execute(statement.offset((page - 1) * page_size).limit(page_size))).all()
    return Page[CustomerListItemResponse](
        items=[
            CustomerListItemResponse(
                phone=customer.phone,
                name=customer.name,
                email=customer.email,
                lifetime_spend_pkr=int(lifetime_spend),
                order_frequency_90d=int(order_frequency),
                last_order_at=last_order_at,
                created_at=customer.created_at,
            )
            for customer, lifetime_spend, order_frequency, last_order_at in rows
        ],
        page=page,
        page_size=page_size,
        total=total or 0,
    )


async def customer_detail(session: AsyncSession, customer_phone: str) -> CustomerDetailResponse:
    customer = await session.scalar(
        select(Customer)
        .where(Customer.phone == customer_phone)
        .options(selectinload(Customer.addresses))
    )
    if customer is None:
        raise not_found("Customer")
    spend, frequency, _last_order = _aggregate_columns()
    totals = (
        await session.execute(
            select(spend, frequency)
            .select_from(Customer)
            .outerjoin(Order, Order.customer_phone == Customer.phone)
            .where(Customer.phone == customer_phone)
            .group_by(Customer.phone)
        )
    ).one()
    cutoff = _ninety_days_ago()
    favorite_rows = (
        await session.execute(
            select(
                OrderItem.product_id,
                OrderItem.product_name,
                func.sum(OrderItem.quantity).label("quantity"),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(
                Order.customer_phone == customer_phone,
                Order.status.in_(REALIZED_ORDER_STATUSES),
                Order.created_at >= cutoff,
            )
            .group_by(OrderItem.product_id, OrderItem.product_name)
            .order_by(desc("quantity"), OrderItem.product_name)
            .limit(5)
        )
    ).all()
    order_rows = (
        await session.execute(
            select(Order, func.count(OrderItem.id).label("item_count"))
            .outerjoin(OrderItem, OrderItem.order_id == Order.id)
            .where(Order.customer_phone == customer_phone)
            .group_by(Order.id)
            .order_by(Order.created_at.desc(), Order.id.desc())
        )
    ).all()
    return CustomerDetailResponse(
        phone=customer.phone,
        name=customer.name,
        email=customer.email,
        address=customer.address,
        lifetime_spend_pkr=int(totals.lifetime_spend_pkr),
        order_frequency_90d=int(totals.order_frequency_90d),
        favorite_items=[
            FavoriteItemResponse(product_id=product_id, product_name=name, quantity=quantity)
            for product_id, name, quantity in favorite_rows
        ],
        addresses=sorted(
            customer.addresses,
            key=lambda address: (not address.is_default, address.created_at),
        ),
        orders=[
            CustomerOrderSummaryResponse(
                id=order.id,
                status=order.status,
                total_pkr=order.total_pkr,
                item_count=int(item_count),
                created_at=order.created_at,
            )
            for order, item_count in order_rows
        ],
        created_at=customer.created_at,
        updated_at=customer.updated_at,
    )
