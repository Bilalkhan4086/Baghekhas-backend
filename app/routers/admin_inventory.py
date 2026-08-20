"""Protected HTTP surface for purchases and batch-based inventory operations."""

import uuid
from datetime import date

from fastapi import APIRouter, Query, status

from app.dependencies import CurrentAdmin, SessionDep
from app.enums import InventoryMovementType, PurchaseStatus
from app.schemas.common import Page
from app.schemas.inventory import (
    InventoryAdjustmentCreate,
    InventoryBatchResponse,
    InventoryMovementDetailResponse,
    InventorySummaryResponse,
    ManualProcurementItemCreate,
    ManualProcurementItemResponse,
    ProcurementRequirementResponse,
    PurchaseCreate,
    PurchasePage,
    PurchaseResponse,
    PurchaseUpdate,
    WasteCreate,
    WastePage,
    WasteResultResponse,
)
from app.services.inventory import (
    InventoryReadService,
    PurchaseCostDraft,
    PurchaseItemDraft,
    PurchaseService,
    WasteService,
)
from app.services.procurement import ManualProcurementService

router = APIRouter(prefix="/admin", tags=["admin inventory"])


async def _clear_read_transaction(session: SessionDep) -> None:
    """Services own mutation transactions; auth lookup may have opened a read one."""
    if session.in_transaction():
        await session.rollback()


def _purchase_items(payload: PurchaseCreate) -> list[PurchaseItemDraft]:
    return [
        PurchaseItemDraft(
            product_id=item.product_id,
            quantity=item.quantity,
            unit_cost=item.unit_cost,
            manual_overhead=item.manual_overhead,
        )
        for item in payload.items
    ]


def _purchase_costs(payload: PurchaseCreate) -> list[PurchaseCostDraft]:
    return [
        PurchaseCostDraft(cost_type=cost.cost_type.value, amount=cost.amount, notes=cost.notes)
        for cost in payload.additional_costs
    ]


@router.post("/purchases", response_model=PurchaseResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase(
    payload: PurchaseCreate, session: SessionDep, admin: CurrentAdmin
) -> PurchaseResponse:
    admin_id = admin.id
    await _clear_read_transaction(session)
    purchase = await PurchaseService(session).create_purchase(
        supplier=payload.supplier,
        purchase_date=payload.purchase_date,
        items=_purchase_items(payload),
        additional_costs=_purchase_costs(payload),
        created_by_id=admin_id,
        cost_allocation_method=payload.cost_allocation_method,
        notes=payload.notes,
    )
    return await InventoryReadService(session).purchase_detail(purchase.id)


@router.put("/purchases/{purchase_id}", response_model=PurchaseResponse)
async def update_purchase(
    purchase_id: uuid.UUID,
    payload: PurchaseUpdate,
    session: SessionDep,
    _admin: CurrentAdmin,
) -> PurchaseResponse:
    await _clear_read_transaction(session)
    purchase = await PurchaseService(session).update_purchase(
        purchase_id,
        supplier=payload.supplier,
        purchase_date=payload.purchase_date,
        items=_purchase_items(payload),
        additional_costs=_purchase_costs(payload),
        cost_allocation_method=payload.cost_allocation_method,
        notes=payload.notes,
    )
    return await InventoryReadService(session).purchase_detail(purchase.id)


@router.delete("/purchases/{purchase_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_purchase(
    purchase_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> None:
    await _clear_read_transaction(session)
    await PurchaseService(session).delete_purchase(purchase_id)


@router.post("/purchases/{purchase_id}/receive", response_model=PurchaseResponse)
async def receive_purchase(
    purchase_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> PurchaseResponse:
    await _clear_read_transaction(session)
    purchase = await PurchaseService(session).receive_purchase(purchase_id)
    return await InventoryReadService(session).purchase_detail(purchase.id)


@router.post("/purchases/{purchase_id}/cancel", response_model=PurchaseResponse)
async def cancel_purchase(
    purchase_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> PurchaseResponse:
    await _clear_read_transaction(session)
    purchase = await PurchaseService(session).cancel_purchase(purchase_id)
    return await InventoryReadService(session).purchase_detail(purchase.id)


@router.get("/purchases", response_model=PurchasePage)
async def list_purchases(
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status: PurchaseStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    supplier: str | None = Query(default=None, max_length=200),
) -> PurchasePage:
    return await InventoryReadService(session).list_purchases(
        page=page,
        page_size=page_size,
        status=status,
        date_from=date_from,
        date_to=date_to,
        supplier=supplier,
    )


@router.get("/purchases/{purchase_id}", response_model=PurchaseResponse)
async def get_purchase(
    purchase_id: uuid.UUID, session: SessionDep, _admin: CurrentAdmin
) -> PurchaseResponse:
    return await InventoryReadService(session).purchase_detail(purchase_id)


@router.get("/products/{product_id}/inventory", response_model=InventorySummaryResponse)
async def get_inventory_summary(
    product_id: str, session: SessionDep, _admin: CurrentAdmin
) -> InventorySummaryResponse:
    return await InventoryReadService(session).inventory_summary(product_id)


@router.get("/products/{product_id}/batches", response_model=list[InventoryBatchResponse])
async def list_batches(
    product_id: str, session: SessionDep, _admin: CurrentAdmin
) -> list[InventoryBatchResponse]:
    return await InventoryReadService(session).batches(product_id)


@router.get(
    "/products/{product_id}/movements",
    response_model=Page[InventoryMovementDetailResponse],
)
async def list_movements(
    product_id: str,
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    movement_type: InventoryMovementType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> Page[InventoryMovementDetailResponse]:
    return await InventoryReadService(session).movements(
        product_id,
        page=page,
        page_size=page_size,
        movement_type=movement_type,
        date_from=date_from,
        date_to=date_to,
    )


@router.post(
    "/inventory/waste",
    response_model=WasteResultResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_waste(
    payload: WasteCreate, session: SessionDep, admin: CurrentAdmin
) -> WasteResultResponse:
    admin_id = admin.id
    await _clear_read_transaction(session)
    result = await WasteService(session).record_waste(
        product_id=payload.product_id,
        quantity=payload.quantity,
        reason=payload.reason,
        notes=payload.notes,
        admin_id=admin_id,
    )
    return WasteResultResponse.model_validate(result)


@router.get("/inventory/waste", response_model=WastePage)
async def list_waste(
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> WastePage:
    return await InventoryReadService(session).waste_records(page=page, page_size=page_size)


@router.post(
    "/inventory/adjustments",
    response_model=InventoryMovementDetailResponse,
    status_code=status.HTTP_201_CREATED,
)
async def record_adjustment(
    payload: InventoryAdjustmentCreate, session: SessionDep, admin: CurrentAdmin
) -> InventoryMovementDetailResponse:
    admin_id = admin.id
    await _clear_read_transaction(session)
    result = await WasteService(session).record_adjustment(
        product_id=payload.product_id,
        quantity_delta=payload.delta,
        reason=payload.reason.value,
        notes=payload.notes,
        admin_id=admin_id,
        inventory_reason=payload.reason,
        reference_order_id=payload.reference_order_id,
    )
    return InventoryMovementDetailResponse.model_validate(result.movements[-1])


@router.get("/inventory/procurement", response_model=list[ProcurementRequirementResponse])
async def get_procurement_requirements(
    session: SessionDep, _admin: CurrentAdmin
) -> list[ProcurementRequirementResponse]:
    return await InventoryReadService(session).procurement_requirements()


@router.post(
    "/inventory/procurement-items",
    response_model=ManualProcurementItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def set_manual_procurement_item(
    payload: ManualProcurementItemCreate,
    session: SessionDep,
    admin: CurrentAdmin,
) -> ManualProcurementItemResponse:
    admin_id = admin.id
    await _clear_read_transaction(session)
    return await ManualProcurementService(session).set_item(
        product_id=payload.product_id,
        quantity=payload.quantity,
        note=payload.note,
        admin_id=admin_id,
    )


@router.delete(
    "/inventory/procurement-items/{product_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_manual_procurement_item(
    product_id: str,
    session: SessionDep,
    _admin: CurrentAdmin,
) -> None:
    await _clear_read_transaction(session)
    await ManualProcurementService(session).remove_item(product_id)
