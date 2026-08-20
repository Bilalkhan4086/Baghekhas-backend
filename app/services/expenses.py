import uuid
from datetime import UTC, date, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ExpenseCategory, ExpenseStatus
from app.exceptions import DomainError, not_found
from app.models import Expense
from app.schemas.expenses import ExpenseCreate, ExpensePage, ExpenseResponse


class ExpenseService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, payload: ExpenseCreate, admin_id: uuid.UUID) -> Expense:
        async with self.session.begin():
            expense = Expense(
                expense_date=payload.expense_date,
                category=payload.category.value,
                description=payload.description,
                amount_pkr=payload.amount_pkr,
                vendor=payload.vendor,
                notes=payload.notes,
                status=ExpenseStatus.ACTIVE.value,
                created_by_id=admin_id,
            )
            self.session.add(expense)
        return expense

    async def void(self, expense_id: uuid.UUID, admin_id: uuid.UUID) -> Expense:
        async with self.session.begin():
            expense = await self.session.scalar(
                select(Expense).where(Expense.id == expense_id).with_for_update()
            )
            if expense is None:
                raise not_found("Expense")
            if expense.status == ExpenseStatus.VOIDED.value:
                raise DomainError(409, "expense_already_voided", "Expense is already voided")
            voided_at = datetime.now(UTC)
            expense.status = ExpenseStatus.VOIDED.value
            expense.voided_at = voided_at
            expense.voided_by_id = admin_id
            # Keep the response fully loaded after commit. Relying on the SQL expression in
            # TimestampMixin.onupdate expires this attribute and can trigger async IO while
            # Pydantic serializes the returned ORM object.
            expense.updated_at = voided_at
        return expense

    async def list(
        self,
        *,
        page: int,
        page_size: int,
        status: ExpenseStatus | None,
        category: ExpenseCategory | None,
        date_from: date | None,
        date_to: date | None,
        query: str | None,
    ) -> ExpensePage:
        filters = []
        if status is not None:
            filters.append(Expense.status == status.value)
        if category is not None:
            filters.append(Expense.category == category.value)
        if date_from is not None:
            filters.append(Expense.expense_date >= date_from)
        if date_to is not None:
            filters.append(Expense.expense_date <= date_to)
        if query:
            term = f"%{query.strip()}%"
            filters.append(or_(Expense.description.ilike(term), Expense.vendor.ilike(term)))

        total, total_amount = (
            await self.session.execute(
                select(
                    func.count(Expense.id),
                    func.coalesce(func.sum(Expense.amount_pkr), 0),
                ).where(*filters)
            )
        ).one()
        expenses = list(
            (
                await self.session.scalars(
                    select(Expense)
                    .where(*filters)
                    .order_by(Expense.expense_date.desc(), Expense.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).all()
        )
        return ExpensePage(
            items=[ExpenseResponse.model_validate(expense) for expense in expenses],
            page=page,
            page_size=page_size,
            total=int(total or 0),
            total_amount_pkr=int(total_amount or 0),
        )
