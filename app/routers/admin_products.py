from fastapi import APIRouter, Query, status

from app.dependencies import CurrentAdmin, SessionDep
from app.enums import InventoryMode, PublicationStatus
from app.models import Product
from app.schemas.common import Page
from app.schemas.products import (
    AdminProductResponse,
    InventoryAdjustmentCreate,
    InventoryMovementResponse,
    InventoryTrackingUpdate,
    ProductCreate,
    ProductUpdate,
)
from app.services.products import (
    adjust_inventory,
    archive_product,
    create_product,
    get_product_for_admin,
    list_admin_products,
    list_inventory_movements,
    update_inventory_tracking,
    update_product,
)

router = APIRouter(prefix="/admin/products", tags=["admin products"])


@router.get("", response_model=Page[AdminProductResponse])
async def list_products(
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    q: str | None = Query(default=None, max_length=200),
    publication_status: PublicationStatus | None = None,
    inventory_mode: InventoryMode | None = None,
    low_stock_only: bool = False,
) -> Page[AdminProductResponse]:
    return await list_admin_products(
        session,
        page=page,
        page_size=page_size,
        query=q,
        publication_status=publication_status.value if publication_status else None,
        inventory_mode=inventory_mode.value if inventory_mode else None,
        low_stock_only=low_stock_only,
    )


@router.post("", response_model=AdminProductResponse, status_code=status.HTTP_201_CREATED)
async def add_product(payload: ProductCreate, session: SessionDep, admin: CurrentAdmin) -> Product:
    return await create_product(session, payload, admin)


@router.get("/{product_id}", response_model=AdminProductResponse)
async def get_product(product_id: str, session: SessionDep, _admin: CurrentAdmin) -> Product:
    return await get_product_for_admin(session, product_id)


@router.patch("/{product_id}", response_model=AdminProductResponse)
async def edit_product(
    product_id: str,
    payload: ProductUpdate,
    session: SessionDep,
    _admin: CurrentAdmin,
) -> Product:
    return await update_product(session, product_id, payload)


@router.post("/{product_id}/archive", response_model=AdminProductResponse)
async def archive(product_id: str, session: SessionDep, _admin: CurrentAdmin) -> Product:
    return await archive_product(session, product_id)


@router.put("/{product_id}/inventory-mode", response_model=AdminProductResponse)
async def change_inventory_mode(
    product_id: str,
    payload: InventoryTrackingUpdate,
    session: SessionDep,
    admin: CurrentAdmin,
) -> Product:
    return await update_inventory_tracking(session, product_id, payload, admin)


@router.post(
    "/{product_id}/inventory-adjustments",
    response_model=InventoryMovementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_inventory_adjustment(
    product_id: str,
    payload: InventoryAdjustmentCreate,
    session: SessionDep,
    admin: CurrentAdmin,
) -> InventoryMovementResponse:
    movement = await adjust_inventory(session, product_id, payload, admin)
    return InventoryMovementResponse.model_validate(movement)


@router.get("/{product_id}/inventory-movements", response_model=Page[InventoryMovementResponse])
async def get_inventory_movements(
    product_id: str,
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> Page[InventoryMovementResponse]:
    return await list_inventory_movements(session, product_id, page=page, page_size=page_size)
