"""Admin contracts for the batch-based inventory engine."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import Field, field_validator

from app.enums import (
    InventoryMovementType,
    InventoryReason,
    PurchaseCostAllocationMethod,
    PurchaseCostType,
    PurchaseStatus,
    WasteReason,
)
from app.schemas.common import APIModel, Page
from app.schemas.products import Quantity, validate_quantity_precision


class PurchaseItemCreate(APIModel):
    product_id: str = Field(min_length=1, max_length=120)
    quantity: Quantity
    unit_cost: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    manual_overhead: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: Decimal) -> Decimal:
        value = validate_quantity_precision(value)
        if value <= 0:
            raise ValueError("Quantity must be greater than zero")
        return value


class PurchaseCostCreate(APIModel):
    cost_type: PurchaseCostType
    amount: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    notes: str | None = Field(default=None, max_length=2000)


class PurchaseCreate(APIModel):
    supplier: str = Field(min_length=1, max_length=200)
    purchase_date: date
    items: list[PurchaseItemCreate] = Field(min_length=1)
    additional_costs: list[PurchaseCostCreate] = Field(default_factory=list)
    cost_allocation_method: PurchaseCostAllocationMethod = PurchaseCostAllocationMethod.BY_WEIGHT
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("supplier")
    @classmethod
    def strip_supplier(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Supplier is required")
        return value


class PurchaseUpdate(PurchaseCreate):
    """Full replacement payload for an editable draft purchase."""


class PurchaseItemResponse(APIModel):
    id: uuid.UUID
    product_id: str
    quantity: Quantity
    unit_cost: Decimal
    line_cost: Decimal
    manual_overhead: Decimal | None


class PurchaseCostResponse(APIModel):
    id: uuid.UUID
    cost_type: PurchaseCostType
    amount: Decimal
    notes: str | None


class InventoryBatchResponse(APIModel):
    id: uuid.UUID
    product_id: str
    purchase_item_id: uuid.UUID | None
    received_quantity: Quantity
    remaining_quantity: Quantity
    unit_cost: Decimal
    effective_cost: Decimal
    received_at: datetime
    created_at: datetime


class PurchaseResponse(APIModel):
    id: uuid.UUID
    purchase_number: str
    supplier: str
    purchase_date: date
    notes: str | None
    subtotal: Decimal
    additional_cost: Decimal
    total_cost: Decimal
    cost_allocation_method: PurchaseCostAllocationMethod
    status: PurchaseStatus
    created_by_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    items: list[PurchaseItemResponse] = Field(default_factory=list)
    costs: list[PurchaseCostResponse] = Field(default_factory=list)
    batches: list[InventoryBatchResponse] = Field(default_factory=list)


class InventorySummaryResponse(APIModel):
    product_id: str
    physical: Quantity
    reserved: Quantity
    available: Quantity
    incoming: Quantity


class InventoryMovementDetailResponse(APIModel):
    id: uuid.UUID
    product_id: str
    batch_id: uuid.UUID | None
    delta: Quantity
    resulting_quantity: Quantity
    reason: InventoryReason
    movement_type: InventoryMovementType | None
    note: str | None
    reference_type: str | None
    reference_id: uuid.UUID | None
    reference_order_id: uuid.UUID | None
    actor_admin_id: uuid.UUID | None
    created_at: datetime


class InventoryAdjustmentCreate(APIModel):
    product_id: str = Field(min_length=1, max_length=120)
    delta: Quantity
    reason: InventoryReason
    notes: str | None = Field(default=None, max_length=500)
    reference_order_id: uuid.UUID | None = None

    @field_validator("delta")
    @classmethod
    def validate_delta(cls, value: Decimal) -> Decimal:
        value = validate_quantity_precision(value)
        if value == 0:
            raise ValueError("Inventory adjustment cannot be zero")
        return value


class WasteCreate(APIModel):
    product_id: str = Field(min_length=1, max_length=120)
    quantity: Quantity
    reason: WasteReason
    notes: str | None = Field(default=None, max_length=5000)

    @field_validator("quantity")
    @classmethod
    def validate_quantity(cls, value: Decimal) -> Decimal:
        value = validate_quantity_precision(value)
        if value <= 0:
            raise ValueError("Waste quantity must be greater than zero")
        return value


class WasteRecordResponse(APIModel):
    id: uuid.UUID
    product_id: str
    batch_id: uuid.UUID | None
    quantity: Quantity
    reason: WasteReason
    notes: str | None
    cost: Decimal
    admin_id: uuid.UUID
    created_at: datetime


class WasteResultResponse(APIModel):
    records: list[WasteRecordResponse]
    cost: Decimal


class ProcurementRequirementResponse(APIModel):
    product_id: str
    product_name: str
    unit_label: str
    current_stock_quantity: Quantity
    projected_stock_quantity: Quantity
    pending_order_quantity: Quantity
    shortage_quantity: Quantity
    low_stock_replenishment_quantity: Quantity
    suggested_purchase_quantity: Quantity
    low_stock_threshold: Quantity
    affected_order_count: int
    pending_order_count: int
    procurement_in_progress: bool
    order_ids: list[uuid.UUID]


InventoryMovementPage = Page[InventoryMovementDetailResponse]
PurchasePage = Page[PurchaseResponse]
WastePage = Page[WasteRecordResponse]
