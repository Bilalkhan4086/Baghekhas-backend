import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, time
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from math import asin, cos, radians, sin, sqrt
from typing import cast

from sqlalchemy import String, func, or_, select, update
from sqlalchemy import cast as sql_cast
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.sql import Select

from app.enums import (
    ORDER_TRANSITIONS,
    FulfillmentStatus,
    InventoryMode,
    OrderStatus,
    RouteStopStatus,
    StockPolicy,
)
from app.exceptions import DomainError, not_found
from app.models import (
    AdminUser,
    Customer,
    CustomerAddress,
    Order,
    OrderItem,
    OrderStatusHistory,
    Product,
    RouteStop,
    generate_order_number,
)
from app.schemas.common import Page
from app.schemas.orders import (
    OrderAdminUpdate,
    OrderCreate,
    OrderSummaryResponse,
    OrderTrackingRequest,
    PublicOrderTrackingEvent,
    PublicOrderTrackingItem,
    PublicOrderTrackingResponse,
)
from app.services.delivery import (
    KARACHI,
    DeliveryScheduleService,
    DeliveryZoneService,
    RiderAssignmentService,
)

DELIVERY_ORIGIN_LATITUDE = Decimal("31.469437737970402")
DELIVERY_ORIGIN_LONGITUDE = Decimal("74.27264727389156")
FREE_DELIVERY_RADIUS_KM = Decimal("1")
DELIVERY_TIER_SIZE_KM = Decimal("2")
DELIVERY_TIER_CHARGE_PKR = 50
MAXIMUM_DELIVERY_CHARGE_PKR = 350
EARTH_RADIUS_KM = 6371.0088
ORDER_NUMBER_GENERATION_ATTEMPTS = 10


async def generate_unique_order_number(session: AsyncSession) -> str:
    for _ in range(ORDER_NUMBER_GENERATION_ATTEMPTS):
        order_number = generate_order_number()
        exists = await session.scalar(
            select(Order.id).where(Order.order_number == order_number).limit(1)
        )
        if exists is None:
            return order_number
    raise DomainError(
        503,
        "order_number_unavailable",
        "An order number could not be allocated; please retry",
    )


def normalize_pakistani_phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    if re.fullmatch(r"03\d{9}", digits):
        return f"+92{digits[1:]}"
    if re.fullmatch(r"923\d{9}", digits):
        return f"+{digits}"
    if re.fullmatch(r"3\d{9}", digits):
        return f"+92{digits}"
    raise DomainError(422, "invalid_phone", "Enter a valid Pakistani mobile number")


def calculate_line_total(unit_price_pkr: int, quantity: Decimal) -> int:
    total = (Decimal(unit_price_pkr) * quantity).quantize(Decimal("1"), ROUND_HALF_UP)
    return int(total)


def requires_current_stock(product: Product) -> bool:
    """Return whether checkout must reject quantities above currently free stock."""
    return (
        product.inventory_mode == InventoryMode.TRACKED.value
        and product.effective_stock_policy == StockPolicy.IN_STOCK_ONLY.value
    )


def calculate_delivery_distance_km(latitude: Decimal, longitude: Decimal) -> Decimal:
    origin_latitude = radians(float(DELIVERY_ORIGIN_LATITUDE))
    destination_latitude = radians(float(latitude))
    latitude_delta = destination_latitude - origin_latitude
    longitude_delta = radians(float(longitude - DELIVERY_ORIGIN_LONGITUDE))
    haversine = sin(latitude_delta / 2) ** 2 + (
        cos(origin_latitude) * cos(destination_latitude) * sin(longitude_delta / 2) ** 2
    )
    haversine = min(1.0, max(0.0, haversine))
    distance = 2 * EARTH_RADIUS_KM * asin(sqrt(haversine))
    return Decimal(str(distance))


def calculate_delivery_charge(distance_km: Decimal) -> int:
    if distance_km <= FREE_DELIVERY_RADIUS_KM:
        return 0
    paid_tiers = int(
        ((distance_km - FREE_DELIVERY_RADIUS_KM) / DELIVERY_TIER_SIZE_KM).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    return min(paid_tiers * DELIVERY_TIER_CHARGE_PKR, MAXIMUM_DELIVERY_CHARGE_PKR)


def calculate_delivery_quote(latitude: Decimal, longitude: Decimal) -> tuple[Decimal, int]:
    raw_distance = calculate_delivery_distance_km(latitude, longitude)
    stored_distance = raw_distance.quantize(Decimal("0.001"), ROUND_HALF_UP)
    return stored_distance, calculate_delivery_charge(raw_distance)


def resolve_delivery_charge(calculated_charge_pkr: int, override_pkr: int | None) -> int:
    return override_pkr if override_pkr is not None else calculated_charge_pkr


def build_request_hash(
    payload: OrderCreate,
    normalized_phone: str,
    delivery_charge_override_pkr: int | None = None,
) -> str:
    canonical = {
        "customer": {
            "name": payload.customer.name,
            "phone": normalized_phone,
            "address": payload.customer.address,
            "notes": payload.customer.notes,
        },
        "delivery_location": {
            "latitude": format(payload.delivery_location.latitude, "f"),
            "longitude": format(payload.delivery_location.longitude, "f"),
        },
        "items": sorted(
            [
                {"product_id": item.product_id, "quantity": format(item.quantity, "f")}
                for item in payload.items
            ],
            key=lambda item: item["product_id"],
        ),
    }
    if delivery_charge_override_pkr is not None:
        canonical["delivery_charge_override_pkr"] = delivery_charge_override_pkr
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def order_load_statement(order_id: uuid.UUID) -> Select[tuple[Order]]:
    return (
        select(Order)
        .where(Order.id == order_id)
        .options(
            selectinload(Order.customer),
            selectinload(Order.items),
            selectinload(Order.status_history),
            selectinload(Order.fulfillment_lines),
        )
        .execution_options(populate_existing=True)
    )


async def get_order_by_idempotency_key(
    session: AsyncSession, idempotency_key: uuid.UUID
) -> Order | None:
    order_id = await session.scalar(
        select(Order.id).where(Order.idempotency_key == idempotency_key)
    )
    if order_id is None:
        return None
    return cast(Order | None, await session.scalar(order_load_statement(order_id)))


async def _assign_initial_rider(session: AsyncSession, order: Order) -> None:
    """Assign the least-loaded active rider in the resolved delivery zone, when available."""
    rider = await RiderAssignmentService(session).select_rider_for_order(order)
    if rider is not None:
        order.rider_id = rider.id


async def create_order(
    session: AsyncSession,
    payload: OrderCreate,
    *,
    idempotency_key: uuid.UUID,
    user_agent: str | None,
    delivery_cutoff_hour: int = 15,
    delivery_default_time: time = time(18, 0),
    delivery_charge_override_pkr: int | None = None,
) -> tuple[Order, bool]:
    phone = normalize_pakistani_phone(payload.customer.phone)
    request_hash = build_request_hash(payload, phone, delivery_charge_override_pkr)
    existing = await get_order_by_idempotency_key(session, idempotency_key)
    if existing is not None:
        if existing.request_hash != request_hash:
            raise DomainError(
                409,
                "idempotency_conflict",
                "This idempotency key was already used for a different order",
            )
        return existing, False

    product_ids = [item.product_id for item in payload.items]
    products = (await session.scalars(select(Product).where(Product.id.in_(product_ids)))).all()
    products_by_id = {product.id: product for product in products}

    order_items: list[OrderItem] = []
    subtotal_pkr = 0
    for input_item in payload.items:
        product = products_by_id.get(input_item.product_id)
        if product is None or not product.available:
            raise DomainError(
                422,
                "product_unavailable",
                f"Product {input_item.product_id} is unavailable",
            )
        if requires_current_stock(product) and input_item.quantity > product.stock_quantity:
            raise DomainError(
                422,
                "insufficient_stock",
                f"Product {input_item.product_id} does not have enough stock",
            )
        line_total = calculate_line_total(product.base_price_pkr, input_item.quantity)
        subtotal_pkr += line_total
        order_items.append(
            OrderItem(
                product_id=product.id,
                product_name=product.name,
                unit_price_pkr=product.base_price_pkr,
                quantity=input_item.quantity,
                line_total_pkr=line_total,
            )
        )

    await session.execute(
        pg_insert(Customer)
        .values(
            phone=phone,
            name=payload.customer.name,
            address=payload.customer.address,
        )
        .on_conflict_do_update(
            index_elements=[Customer.phone],
            set_={
                "name": payload.customer.name,
                "address": payload.customer.address,
                "updated_at": func.now(),
            },
        )
    )

    saved_address = await session.scalar(
        select(CustomerAddress)
        .where(
            CustomerAddress.customer_phone == phone,
            CustomerAddress.address_text == payload.customer.address,
        )
        .with_for_update()
    )
    await session.execute(
        update(CustomerAddress)
        .where(CustomerAddress.customer_phone == phone, CustomerAddress.is_default.is_(True))
        .values(is_default=False)
    )
    if saved_address is None:
        session.add(
            CustomerAddress(
                customer_phone=phone,
                label="Delivery address",
                address_text=payload.customer.address,
                latitude=payload.delivery_location.latitude,
                longitude=payload.delivery_location.longitude,
                is_default=True,
            )
        )
    else:
        saved_address.latitude = payload.delivery_location.latitude
        saved_address.longitude = payload.delivery_location.longitude
        saved_address.is_default = True

    delivery_distance_km, calculated_delivery_charge_pkr = calculate_delivery_quote(
        payload.delivery_location.latitude,
        payload.delivery_location.longitude,
    )
    delivery_charge_pkr = resolve_delivery_charge(
        calculated_delivery_charge_pkr,
        delivery_charge_override_pkr,
    )
    delivery_zone = await DeliveryZoneService(session).resolve_zone(
        float(payload.delivery_location.latitude),
        float(payload.delivery_location.longitude),
    )
    delivery_schedule = DeliveryScheduleService(delivery_cutoff_hour, delivery_default_time)
    order = Order(
        order_number=await generate_unique_order_number(session),
        customer_phone=phone,
        customer_name_snapshot=payload.customer.name,
        delivery_address_snapshot=payload.customer.address,
        notes=payload.customer.notes,
        status=OrderStatus.PENDING.value,
        subtotal_pkr=subtotal_pkr,
        delivery_charge_pkr=delivery_charge_pkr,
        delivery_distance_km=delivery_distance_km,
        delivery_latitude=payload.delivery_location.latitude,
        delivery_longitude=payload.delivery_location.longitude,
        total_pkr=subtotal_pkr + delivery_charge_pkr,
        user_agent=user_agent[:500] if user_agent else None,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        delivery_zone_id=delivery_zone.id if delivery_zone is not None else None,
        promised_delivery_date=delivery_schedule.calculate_delivery_date(datetime.now(UTC)),
        promised_delivery_time=delivery_schedule.calculate_delivery_time(),
        items=order_items,
    )
    await _assign_initial_rider(session, order)
    session.add(order)
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        concurrent = await get_order_by_idempotency_key(session, idempotency_key)
        if concurrent is None:
            raise
        if concurrent.request_hash != request_hash:
            raise DomainError(
                409,
                "idempotency_conflict",
                "This idempotency key was already used for a different order",
            ) from error
        return concurrent, False

    created = await session.scalar(order_load_statement(order.id))
    assert created is not None
    return created, True


async def get_order_for_admin(session: AsyncSession, order_id: uuid.UUID) -> Order:
    order = cast(Order | None, await session.scalar(order_load_statement(order_id)))
    if order is None:
        raise not_found("Order")
    return order


async def list_admin_orders(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    status: str | None,
    internal_fulfillment_status: FulfillmentStatus | None,
    query: str | None,
    date_from: datetime | None,
    date_to: datetime | None,
) -> Page[OrderSummaryResponse]:
    filters = []
    if status:
        filters.append(Order.status == status)
    if internal_fulfillment_status:
        filters.append(Order.internal_fulfillment_status == internal_fulfillment_status.value)
    if date_from:
        filters.append(Order.created_at >= date_from)
    if date_to:
        filters.append(Order.created_at <= date_to)
    if query:
        term = f"%{query.strip()}%"
        filters.append(
            or_(
                Order.order_number.ilike(term),
                Order.customer_name_snapshot.ilike(term),
                Order.customer_phone.ilike(term),
                sql_cast(Order.id, String).ilike(term),
            )
        )

    base = select(Order).where(*filters)
    total = await session.scalar(select(func.count()).select_from(Order).where(*filters))
    item_count = (
        select(func.count(OrderItem.id))
        .where(OrderItem.order_id == Order.id)
        .correlate(Order)
        .scalar_subquery()
    )
    rows = (
        await session.execute(
            base.with_only_columns(Order, item_count)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    items = [
        OrderSummaryResponse(
            id=order.id,
            order_number=order.order_number,
            status=order.status,
            subtotal_pkr=order.subtotal_pkr,
            delivery_charge_pkr=order.delivery_charge_pkr,
            delivery_distance_km=order.delivery_distance_km,
            total_pkr=order.total_pkr,
            refund_amount_pkr=order.refund_amount_pkr,
            internal_fulfillment_status=order.internal_fulfillment_status,
            rider_id=order.rider_id,
            promised_delivery_date=order.promised_delivery_date,
            promised_delivery_time=order.promised_delivery_time,
            customer_phone=order.customer_phone,
            customer_name=order.customer_name_snapshot,
            item_count=count,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )
        for order, count in rows
    ]
    return Page[OrderSummaryResponse](
        items=items,
        page=page,
        page_size=page_size,
        total=total or 0,
    )


async def track_public_order(
    session: AsyncSession, payload: OrderTrackingRequest
) -> PublicOrderTrackingResponse:
    phone = normalize_pakistani_phone(payload.phone)
    order_id = await session.scalar(
        select(Order.id).where(
            Order.order_number == payload.order_number,
            Order.customer_phone == phone,
        )
    )
    if order_id is None:
        raise not_found("Order")
    order = cast(Order | None, await session.scalar(order_load_statement(order_id)))
    if order is None:
        raise not_found("Order")
    return PublicOrderTrackingResponse(
        order_number=order.order_number,
        status=order.status,
        promised_delivery_date=order.promised_delivery_date,
        promised_delivery_time=order.promised_delivery_time,
        items=[
            PublicOrderTrackingItem(
                product_name=item.product_name,
                quantity=item.quantity,
                unit_label=item.unit_label,
            )
            for item in order.items
        ],
        status_history=[
            PublicOrderTrackingEvent(status=event.to_status, created_at=event.created_at)
            for event in order.status_history
        ],
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


async def update_order(
    session: AsyncSession,
    order_id: uuid.UUID,
    payload: OrderAdminUpdate,
    actor: AdminUser,
) -> Order:
    order = await session.scalar(select(Order).where(Order.id == order_id).with_for_update())
    if order is None:
        raise not_found("Order")

    changed = False
    if "admin_note" in payload.model_fields_set:
        order.admin_note = payload.admin_note.strip() if payload.admin_note else None
        changed = True

    schedule_fields = {
        "promised_delivery_date",
        "promised_delivery_time",
    }.intersection(payload.model_fields_set)
    if schedule_fields:
        routed_stop_id = await session.scalar(
            select(RouteStop.id)
            .where(
                RouteStop.order_id == order.id,
                RouteStop.status.in_(
                    [
                        RouteStopStatus.PENDING.value,
                        RouteStopStatus.READY.value,
                        RouteStopStatus.IN_PROGRESS.value,
                    ]
                ),
            )
            .limit(1)
        )
        if routed_stop_id is not None:
            raise DomainError(
                409,
                "routed_order_locked",
                "Cancel the generated route before changing this delivery schedule",
            )
        if order.status in {
            OrderStatus.DELIVERED.value,
            OrderStatus.NOT_RECEIVED.value,
            OrderStatus.COMPLETED.value,
            OrderStatus.CANCELLED.value,
            OrderStatus.REFUNDED.value,
        }:
            raise DomainError(
                409,
                "delivery_schedule_locked",
                "The delivery schedule cannot be changed after delivery is resolved",
            )
        promised_date = (
            payload.promised_delivery_date
            if "promised_delivery_date" in schedule_fields
            else order.promised_delivery_date
        )
        promised_time = (
            payload.promised_delivery_time
            if "promised_delivery_time" in schedule_fields
            else order.promised_delivery_time
        )
        if promised_date is None or promised_time is None:
            raise DomainError(
                422,
                "incomplete_delivery_schedule",
                "Both promised delivery date and time are required",
            )
        promised_at = datetime.combine(promised_date, promised_time, tzinfo=KARACHI)
        if promised_at < datetime.now(KARACHI):
            raise DomainError(
                422,
                "delivery_schedule_in_past",
                "The promised delivery date and time cannot be in the past",
            )
        if (
            promised_date != order.promised_delivery_date
            or promised_time != order.promised_delivery_time
        ):
            order.promised_delivery_date = promised_date
            order.promised_delivery_time = promised_time
            session.add(
                OrderStatusHistory(
                    order_id=order.id,
                    from_status=order.status,
                    to_status=order.status,
                    note=(
                        "Delivery rescheduled for "
                        f"{promised_date.isoformat()} at {promised_time.strftime('%H:%M')} PKT"
                    ),
                    actor_admin_id=actor.id,
                )
            )
            changed = True

    if payload.status is not None:
        if payload.status not in {OrderStatus.COMPLETED, OrderStatus.REFUNDED}:
            raise DomainError(
                409,
                "dedicated_transition_required",
                "Operational status changes require a dedicated order action",
            )
        current_status = OrderStatus(order.status)
        if payload.status == current_status:
            if not changed:
                raise DomainError(409, "status_unchanged", "Order already has this status")
        else:
            if payload.status not in ORDER_TRANSITIONS[current_status]:
                raise DomainError(
                    409,
                    "invalid_status_transition",
                    f"Cannot move an order from {current_status.value} to {payload.status.value}",
                )
            if (
                payload.status == OrderStatus.REFUNDED
                and payload.refund_amount_pkr is not None
                and payload.refund_amount_pkr > order.total_pkr
            ):
                raise DomainError(
                    422,
                    "invalid_refund_amount",
                    "Refund amount cannot exceed the order total",
                )
            order.status = payload.status.value
            if payload.status == OrderStatus.REFUNDED:
                order.refund_amount_pkr = payload.refund_amount_pkr
            session.add(
                OrderStatusHistory(
                    order_id=order.id,
                    from_status=current_status.value,
                    to_status=payload.status.value,
                    note=payload.status_note,
                    actor_admin_id=actor.id,
                )
            )
            changed = True

    if changed:
        order.updated_at = datetime.now(UTC)
    await session.commit()
    return await get_order_for_admin(session, order_id)
