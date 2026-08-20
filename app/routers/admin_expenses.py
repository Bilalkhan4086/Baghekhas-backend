import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.dependencies import CurrentAdmin, SessionDep
from app.enums import ExpenseCategory, ExpenseStatus
from app.schemas.expenses import ExpenseCreate, ExpensePage, ExpenseResponse
from app.services.expenses import ExpenseService

router = APIRouter(prefix="/admin/expenses", tags=["admin expenses"])


async def _clear_read_transaction(session: SessionDep) -> None:
    if session.in_transaction():
        await session.rollback()


@router.get("", response_model=ExpensePage)
async def list_expenses(
    session: SessionDep,
    _admin: CurrentAdmin,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    status_filter: Annotated[ExpenseStatus | None, Query(alias="status")] = ExpenseStatus.ACTIVE,
    category: ExpenseCategory | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = Query(default=None, max_length=200),
) -> ExpensePage:
    return await ExpenseService(session).list(
        page=page,
        page_size=page_size,
        status=status_filter,
        category=category,
        date_from=date_from,
        date_to=date_to,
        query=q,
    )


@router.post("", response_model=ExpenseResponse, status_code=status.HTTP_201_CREATED)
async def create_expense(
    payload: ExpenseCreate,
    session: SessionDep,
    admin: CurrentAdmin,
) -> ExpenseResponse:
    admin_id = admin.id
    await _clear_read_transaction(session)
    expense = await ExpenseService(session).create(payload, admin_id)
    return ExpenseResponse.model_validate(expense)


@router.post("/{expense_id}/void", response_model=ExpenseResponse)
async def void_expense(
    expense_id: uuid.UUID,
    session: SessionDep,
    admin: CurrentAdmin,
) -> ExpenseResponse:
    admin_id = admin.id
    await _clear_read_transaction(session)
    expense = await ExpenseService(session).void(expense_id, admin_id)
    return ExpenseResponse.model_validate(expense)
