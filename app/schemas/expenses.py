import uuid
from datetime import date, datetime

from pydantic import Field, field_validator

from app.enums import ExpenseCategory, ExpenseStatus
from app.schemas.common import APIModel, Page


class ExpenseCreate(APIModel):
    expense_date: date
    category: ExpenseCategory
    description: str = Field(min_length=1, max_length=200)
    amount_pkr: int = Field(gt=0, le=100_000_000)
    vendor: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Description cannot be blank")
        return value

    @field_validator("vendor", "notes")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ExpenseResponse(APIModel):
    id: uuid.UUID
    expense_date: date
    category: ExpenseCategory
    description: str
    amount_pkr: int
    vendor: str | None
    notes: str | None
    status: ExpenseStatus
    created_by_id: uuid.UUID
    voided_at: datetime | None
    voided_by_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ExpensePage(Page[ExpenseResponse]):
    total_amount_pkr: int = Field(ge=0)
