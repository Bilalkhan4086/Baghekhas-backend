from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import func, or_, select

from app.dependencies import SessionDep, SettingsDep
from app.enums import CatalogType, OrderStatus, PublicationStatus
from app.exceptions import not_found
from app.models import Order, OrderItem, Product
from app.schemas.common import Page
from app.schemas.orders import DeliveryQuoteResponse
from app.schemas.products import CatalogProductResponse
from app.services.delivery import DeliveryScheduleService
from app.services.orders import calculate_delivery_quote

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/products", response_model=Page[CatalogProductResponse])
async def list_catalog_products(
    session: SessionDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, max_length=80),
    catalog_type: CatalogType | None = None,
    is_popular: bool | None = None,
    sort: Literal["name", "popular"] = "name",
) -> Page[CatalogProductResponse]:
    filters = [Product.publication_status != PublicationStatus.ARCHIVED.value]
    if q:
        term = f"%{q.strip()}%"
        filters.append(or_(Product.name.ilike(term), Product.id.ilike(term)))
    if category:
        filters.append(Product.category == category)
    if catalog_type:
        filters.append(Product.catalog_type == catalog_type.value)
    if is_popular is not None:
        filters.append(Product.is_popular.is_(is_popular))

    total = await session.scalar(select(func.count()).select_from(Product).where(*filters))
    statement = select(Product).where(*filters)
    if sort == "popular":
        sold_quantities = (
            select(
                OrderItem.product_id,
                func.sum(OrderItem.quantity).label("sold_quantity"),
            )
            .join(Order, Order.id == OrderItem.order_id)
            .where(Order.status.in_([OrderStatus.DELIVERED.value, OrderStatus.COMPLETED.value]))
            .group_by(OrderItem.product_id)
            .subquery()
        )
        statement = statement.outerjoin(
            sold_quantities, sold_quantities.c.product_id == Product.id
        ).order_by(
            func.coalesce(sold_quantities.c.sold_quantity, 0).desc(),
            Product.name.asc(),
            Product.id.asc(),
        )
    else:
        statement = statement.order_by(Product.name.asc(), Product.id.asc())
    products = (
        await session.scalars(
            statement.offset((page - 1) * page_size).limit(page_size)
        )
    ).all()
    return Page[CatalogProductResponse](
        items=[_catalog_response(item) for item in products],
        page=page,
        page_size=page_size,
        total=total or 0,
    )


@router.get("/products/{product_id}", response_model=CatalogProductResponse)
async def get_catalog_product(product_id: str, session: SessionDep) -> CatalogProductResponse:
    product = await session.get(Product, product_id)
    if product is None or product.publication_status == PublicationStatus.ARCHIVED.value:
        raise not_found("Product")
    return _catalog_response(product)


def _catalog_response(product: Product) -> CatalogProductResponse:
    return CatalogProductResponse(
        id=product.id,
        name=product.name,
        description=product.description,
        image_url=product.image_url,
        image_urls=product.image_urls,
        category=product.category,
        catalog_type=product.catalog_type,
        unit_label=product.unit_label,
        tag=product.tag,
        base_price_pkr=product.base_price_pkr,
        compare_at_price_pkr=product.compare_at_price_pkr,
        pricing_type=product.pricing_type,
        publication_status=product.publication_status,
        is_popular=product.is_popular,
        is_available=product.available,
        availability=product.customer_availability,
    )


@router.get("/delivery-preview", response_model=DeliveryQuoteResponse)
async def delivery_preview(
    settings: SettingsDep,
    latitude: Annotated[Decimal, Query(ge=-90, le=90)],
    longitude: Annotated[Decimal, Query(ge=-180, le=180)],
) -> DeliveryQuoteResponse:
    """Return an indicative delivery charge/date; order creation always recalculates both."""
    distance_km, delivery_charge_pkr = calculate_delivery_quote(latitude, longitude)
    delivery_schedule = DeliveryScheduleService(
        settings.delivery_cutoff_hour, settings.delivery_default_time
    )
    return DeliveryQuoteResponse(
        distance_km=distance_km,
        delivery_charge_pkr=delivery_charge_pkr,
        promised_delivery_date=delivery_schedule.calculate_delivery_date(datetime.now(UTC)),
        promised_delivery_time=delivery_schedule.calculate_delivery_time(),
    )
