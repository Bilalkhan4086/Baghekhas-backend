from decimal import Decimal
from typing import cast

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import InventoryMode, InventoryReason, PublicationStatus
from app.exceptions import DomainError, not_found
from app.models import AdminUser, InventoryMovement, Product
from app.schemas.common import Page
from app.schemas.products import (
    AdminProductResponse,
    InventoryAdjustmentCreate,
    InventoryMovementResponse,
    InventoryTrackingUpdate,
    ProductCreate,
    ProductUpdate,
)
from app.services.inventory import (
    InventoryLifecycleService,
    WasteService,
)


def _constraint_name(error: IntegrityError) -> str | None:
    diagnostic = getattr(error.orig, "diag", None)
    return cast(str | None, getattr(diagnostic, "constraint_name", None))


async def get_product_for_admin(session: AsyncSession, product_id: str) -> Product:
    product = await session.get(Product, product_id)
    if product is None:
        raise not_found("Product")
    return product


async def list_admin_products(
    session: AsyncSession,
    *,
    page: int,
    page_size: int,
    query: str | None,
    publication_status: str | None,
    inventory_mode: str | None,
    low_stock_only: bool,
) -> Page[AdminProductResponse]:
    filters = []
    if query:
        term = f"%{query.strip()}%"
        filters.append(or_(Product.name.ilike(term), Product.id.ilike(term)))
    if publication_status:
        filters.append(Product.publication_status == publication_status)
    if inventory_mode:
        filters.append(Product.inventory_mode == inventory_mode)
    if low_stock_only:
        filters.extend(
            [
                Product.inventory_mode == InventoryMode.TRACKED.value,
                Product.stock_quantity <= Product.low_stock_threshold,
            ]
        )

    total = await session.scalar(select(func.count()).select_from(Product).where(*filters))
    products = (
        await session.scalars(
            select(Product)
            .where(*filters)
            .order_by(Product.name.asc(), Product.id.asc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page[AdminProductResponse](
        items=[AdminProductResponse.model_validate(item) for item in products],
        page=page,
        page_size=page_size,
        total=total or 0,
    )


async def create_product(
    session: AsyncSession, payload: ProductCreate, actor: AdminUser
) -> Product:
    # Keep validated Decimal values as Decimal objects for SQLAlchemy model properties.
    # JSON mode converts quantities to strings, which breaks computed properties such as
    # Product.low_stock before the ORM instance is reloaded from PostgreSQL.
    values = payload.model_dump(mode="python", exclude={"opening_stock", "image_urls"})
    image_urls = payload.image_urls or [payload.image_url]
    values["image_url"] = image_urls[0]
    values["gallery_image_urls"] = image_urls[1:]
    values["stock_quantity"] = Decimal("0")
    product = Product(**values)
    actor_id = actor.id
    if session.in_transaction():
        await session.rollback()
    try:
        async with session.begin():
            session.add(product)
            await session.flush()
            if payload.inventory_mode == InventoryMode.TRACKED or payload.opening_stock != 0:
                await InventoryLifecycleService(session).record_opening_balance_in_transaction(
                    product_id=product.id,
                    quantity=payload.opening_stock,
                    admin_id=actor_id,
                    note="Opening inventory balance",
                )
    except IntegrityError as error:
        await session.rollback()
        if _constraint_name(error) == "products_pkey":
            raise DomainError(
                409, "product_exists", "A product with this ID already exists"
            ) from error
        raise
    await session.refresh(product)
    return product


async def update_product(session: AsyncSession, product_id: str, payload: ProductUpdate) -> Product:
    product = await get_product_for_admin(session, product_id)
    values = payload.model_dump(mode="python", exclude_unset=True)
    image_urls = values.pop("image_urls", None)
    if image_urls is not None:
        values["image_url"] = image_urls[0]
        values["gallery_image_urls"] = image_urls[1:]
    elif "image_url" in values:
        # Preserve the legacy single-image replacement contract.
        values["gallery_image_urls"] = []

    if "manual_available" in values and product.inventory_mode == InventoryMode.TRACKED.value:
        raise DomainError(
            409,
            "tracked_availability",
            "Availability for tracked products is derived from stock quantity",
        )

    final_base_price = values.get("base_price_pkr", product.base_price_pkr)
    final_compare_price = values.get("compare_at_price_pkr", product.compare_at_price_pkr)
    if final_compare_price is not None and final_compare_price < final_base_price:
        raise DomainError(
            422,
            "invalid_compare_price",
            "Comparison price cannot be lower than the base price",
        )

    for field, value in values.items():
        setattr(product, field, value)
    await session.commit()
    await session.refresh(product)
    return product


async def archive_product(session: AsyncSession, product_id: str) -> Product:
    product = await get_product_for_admin(session, product_id)
    product.publication_status = PublicationStatus.ARCHIVED.value
    await session.commit()
    await session.refresh(product)
    return product


async def update_inventory_tracking(
    session: AsyncSession,
    product_id: str,
    payload: InventoryTrackingUpdate,
    actor: AdminUser,
) -> Product:
    actor_id = actor.id
    if session.in_transaction():
        await session.rollback()
    return await InventoryLifecycleService(session).set_tracking_mode(
        product_id=product_id,
        tracked=payload.mode == InventoryMode.TRACKED,
        opening_quantity=payload.opening_quantity,
        manual_available=payload.manual_available,
        admin_id=actor_id,
        note=payload.note,
    )


async def adjust_inventory(
    session: AsyncSession,
    product_id: str,
    payload: InventoryAdjustmentCreate,
    actor: AdminUser,
) -> InventoryMovement:
    if payload.reason == InventoryReason.ORDER_FULFILLMENT and payload.reference_order_id is None:
        raise DomainError(
            422,
            "order_reference_required",
            "Order fulfillment adjustments require reference_order_id",
        )

    actor_id = actor.id
    if session.in_transaction():
        await session.rollback()
    result = await WasteService(session).record_adjustment(
        product_id=product_id,
        quantity_delta=payload.delta,
        reason=payload.reason.value,
        notes=payload.note,
        admin_id=actor_id,
        inventory_reason=payload.reason,
        reference_order_id=payload.reference_order_id,
    )
    return result.movements[-1]


async def list_inventory_movements(
    session: AsyncSession, product_id: str, *, page: int, page_size: int
) -> Page[InventoryMovementResponse]:
    if await session.get(Product, product_id) is None:
        raise not_found("Product")
    filters = [InventoryMovement.product_id == product_id]
    total = await session.scalar(
        select(func.count()).select_from(InventoryMovement).where(*filters)
    )
    movements = (
        await session.scalars(
            select(InventoryMovement)
            .where(*filters)
            .order_by(InventoryMovement.created_at.desc(), InventoryMovement.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return Page[InventoryMovementResponse](
        items=[InventoryMovementResponse.model_validate(item) for item in movements],
        page=page,
        page_size=page_size,
        total=total or 0,
    )
