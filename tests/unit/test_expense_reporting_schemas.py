import uuid
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from app.enums import ExpenseStatus
from app.models import Expense
from app.schemas.expenses import ExpenseCreate, ExpenseResponse
from app.schemas.inventory import ManualProcurementItemCreate
from app.services.expenses import ExpenseService
from app.services.reporting import _money


def test_expense_create_strips_text_and_keeps_integer_pkr() -> None:
    expense = ExpenseCreate(
        expense_date=date(2026, 8, 19),
        category="utilities",
        description="  Electricity bill  ",
        amount_pkr=4500,
        vendor="  LESCO  ",
        notes="  August office bill  ",
    )

    assert expense.description == "Electricity bill"
    assert expense.vendor == "LESCO"
    assert expense.notes == "August office bill"
    assert expense.amount_pkr == 4500


@pytest.mark.parametrize("amount", [0, -1, 100_000_001])
def test_expense_create_rejects_invalid_amount(amount: int) -> None:
    with pytest.raises(ValidationError):
        ExpenseCreate(
            expense_date=date(2026, 8, 19),
            category="miscellaneous",
            description="Invalid amount",
            amount_pkr=amount,
        )


class _ExpenseSessionStub:
    def __init__(self, expense: Expense) -> None:
        self.expense = expense

    @asynccontextmanager
    async def begin(self):  # type: ignore[no-untyped-def]
        yield

    async def scalar(self, _statement: object) -> Expense:
        return self.expense


@pytest.mark.asyncio
async def test_void_expense_sets_a_loaded_updated_timestamp_for_response() -> None:
    admin_id = uuid.uuid4()
    original_updated_at = datetime(2026, 8, 19, tzinfo=UTC)
    expense = Expense(
        id=uuid.uuid4(),
        expense_date=date(2026, 8, 19),
        category="utilities",
        description="Electricity bill",
        amount_pkr=4500,
        vendor="LESCO",
        notes=None,
        status=ExpenseStatus.ACTIVE.value,
        created_by_id=admin_id,
        voided_at=None,
        voided_by_id=None,
        created_at=original_updated_at,
        updated_at=original_updated_at,
    )

    result = await ExpenseService(_ExpenseSessionStub(expense)).void(  # type: ignore[arg-type]
        expense.id,
        admin_id,
    )
    response = ExpenseResponse.model_validate(result)

    assert response.status is ExpenseStatus.VOIDED
    assert response.voided_by_id == admin_id
    assert response.updated_at == response.voided_at
    assert response.updated_at > original_updated_at


@pytest.mark.parametrize(
    ("value", "expected"),
    [("10.49", 10), ("10.50", 11), (None, 0)],
)
def test_reporting_money_rounds_half_up(value: str | None, expected: int) -> None:
    assert _money(value) == expected


def test_manual_procurement_item_normalizes_input() -> None:
    item = ManualProcurementItemCreate(
        product_id="  mango  ",
        quantity="2.500",
        note="  Display stock  ",
    )

    assert item.product_id == "mango"
    assert str(item.quantity) == "2.500"
    assert item.note == "Display stock"


def test_manual_procurement_item_rejects_blank_product() -> None:
    with pytest.raises(ValidationError):
        ManualProcurementItemCreate(product_id=" ", quantity="1")
