from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Date, and_, case, cast, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.enums import ExpenseStatus, OrderStatus, PurchaseStatus
from app.models import Expense, InventoryBatch, Order, OrderItem, Product, Purchase, WasteRecord
from app.schemas.reporting import (
    BusinessStatsResponse,
    DailyBusinessTotal,
    ExpenseCategoryTotal,
    ReportSummary,
    StatusTotal,
    TopProductTotal,
)

KARACHI = ZoneInfo("Asia/Karachi")
REALIZED_STATUSES = {OrderStatus.DELIVERED.value, OrderStatus.COMPLETED.value}
ZERO = Decimal("0")


def _money(value: object) -> int:
    return int(Decimal(str(value or 0)).quantize(Decimal("1"), ROUND_HALF_UP))


def _recognized_condition() -> ColumnElement[bool]:
    return (Order.status.in_(REALIZED_STATUSES)) | and_(
        Order.status == OrderStatus.REFUNDED.value,
        Order.cogs_pkr.is_not(None),
    )


def _net_sales_expression() -> ColumnElement[int]:
    return case(
        (Order.status.in_(REALIZED_STATUSES), Order.total_pkr),
        (
            and_(
                Order.status == OrderStatus.REFUNDED.value,
                Order.cogs_pkr.is_not(None),
            ),
            Order.total_pkr - func.coalesce(Order.refund_amount_pkr, 0),
        ),
        else_=0,
    )


class ReportingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def business_stats(self, date_from: date, date_to: date) -> BusinessStatsResponse:
        start = datetime.combine(date_from, time.min, KARACHI).astimezone(UTC)
        end = datetime.combine(date_to + timedelta(days=1), time.min, KARACHI).astimezone(UTC)
        order_range = (Order.created_at >= start, Order.created_at < end)
        recognized = _recognized_condition()
        net_sales = _net_sales_expression()
        gross_sales = case((recognized, Order.total_pkr), else_=0)
        refunds = case(
            (
                and_(
                    Order.status == OrderStatus.REFUNDED.value,
                    Order.cogs_pkr.is_not(None),
                ),
                func.coalesce(Order.refund_amount_pkr, 0),
            ),
            else_=0,
        )
        cogs = case((recognized, func.coalesce(Order.cogs_pkr, 0)), else_=0)

        order_totals = (
            await self.session.execute(
                select(
                    func.count(Order.id),
                    func.count(case((recognized, 1))),
                    func.count(distinct(Order.customer_phone)),
                    func.coalesce(func.sum(gross_sales), 0),
                    func.coalesce(func.sum(refunds), 0),
                    func.coalesce(func.sum(net_sales), 0),
                    func.coalesce(func.sum(cogs), 0),
                ).where(*order_range)
            )
        ).one()
        (
            order_count,
            recognized_count,
            unique_customers,
            gross_sales_total,
            refund_total,
            net_sales_total,
            cogs_total,
        ) = order_totals

        expense_total = await self.session.scalar(
            select(func.coalesce(func.sum(Expense.amount_pkr), 0)).where(
                Expense.status == ExpenseStatus.ACTIVE.value,
                Expense.expense_date >= date_from,
                Expense.expense_date <= date_to,
            )
        )
        waste_total = await self.session.scalar(
            select(func.coalesce(func.sum(WasteRecord.cost), ZERO)).where(
                WasteRecord.created_at >= start,
                WasteRecord.created_at < end,
            )
        )
        received_purchase_total = await self.session.scalar(
            select(func.coalesce(func.sum(Purchase.total_cost), ZERO)).where(
                Purchase.status == PurchaseStatus.RECEIVED.value,
                Purchase.purchase_date >= date_from,
                Purchase.purchase_date <= date_to,
            )
        )
        draft_purchase_total = await self.session.scalar(
            select(func.coalesce(func.sum(Purchase.total_cost), ZERO)).where(
                Purchase.status == PurchaseStatus.DRAFT.value,
                Purchase.purchase_date >= date_from,
                Purchase.purchase_date <= date_to,
            )
        )
        inventory_value = await self.session.scalar(
            select(
                func.coalesce(
                    func.sum(InventoryBatch.remaining_quantity * InventoryBatch.effective_cost),
                    ZERO,
                )
            )
        )
        low_stock_count = await self.session.scalar(
            select(func.count(Product.id)).where(
                Product.inventory_mode == "tracked",
                Product.stock_quantity <= Product.low_stock_threshold,
                Product.publication_status != "archived",
            )
        )

        successful_delivery = recognized
        attempted_delivery = successful_delivery | (Order.status == OrderStatus.NOT_RECEIVED.value)
        delivery_counts = (
            await self.session.execute(
                select(
                    func.count(case((successful_delivery, 1))),
                    func.count(case((attempted_delivery, 1))),
                ).where(*order_range)
            )
        ).one()
        success_count, attempted_count = delivery_counts

        gross_sales_pkr = _money(gross_sales_total)
        refunds_pkr = _money(refund_total)
        net_sales_pkr = _money(net_sales_total)
        cogs_pkr = _money(cogs_total)
        expenses_pkr = _money(expense_total)
        waste_cost_pkr = _money(waste_total)
        gross_profit_pkr = net_sales_pkr - cogs_pkr

        status_rows = (
            await self.session.execute(
                select(Order.status, func.count(Order.id))
                .where(*order_range)
                .group_by(Order.status)
            )
        ).all()
        counts_by_status = {status: int(count) for status, count in status_rows}

        order_day = cast(func.timezone("Asia/Karachi", Order.created_at), Date)
        daily_order_rows = (
            await self.session.execute(
                select(
                    order_day.label("day"),
                    func.count(Order.id),
                    func.coalesce(func.sum(net_sales), 0),
                )
                .where(*order_range)
                .group_by(order_day)
                .order_by(order_day)
            )
        ).all()
        daily_expense_rows = (
            await self.session.execute(
                select(Expense.expense_date, func.coalesce(func.sum(Expense.amount_pkr), 0))
                .where(
                    Expense.status == ExpenseStatus.ACTIVE.value,
                    Expense.expense_date >= date_from,
                    Expense.expense_date <= date_to,
                )
                .group_by(Expense.expense_date)
                .order_by(Expense.expense_date)
            )
        ).all()
        daily_orders = {
            day: (int(count), _money(day_sales))
            for day, count, day_sales in daily_order_rows
        }
        daily_expenses = {day: _money(amount) for day, amount in daily_expense_rows}
        days = (date_to - date_from).days + 1
        daily = [
            DailyBusinessTotal(
                date=day,
                order_count=daily_orders.get(day, (0, 0))[0],
                net_sales_pkr=daily_orders.get(day, (0, 0))[1],
                expenses_pkr=daily_expenses.get(day, 0),
            )
            for day in (date_from + timedelta(days=offset) for offset in range(days))
        ]

        top_rows = (
            await self.session.execute(
                select(
                    OrderItem.product_id,
                    OrderItem.product_name,
                    OrderItem.unit_label,
                    func.sum(OrderItem.quantity).label("quantity"),
                    func.sum(OrderItem.line_total_pkr).label("revenue"),
                )
                .join(Order, Order.id == OrderItem.order_id)
                .where(
                    *order_range,
                    Order.status.in_(REALIZED_STATUSES),
                )
                .group_by(OrderItem.product_id, OrderItem.product_name, OrderItem.unit_label)
                .order_by(func.sum(OrderItem.line_total_pkr).desc(), OrderItem.product_name)
                .limit(8)
            )
        ).all()
        expense_rows = (
            await self.session.execute(
                select(Expense.category, func.sum(Expense.amount_pkr))
                .where(
                    Expense.status == ExpenseStatus.ACTIVE.value,
                    Expense.expense_date >= date_from,
                    Expense.expense_date <= date_to,
                )
                .group_by(Expense.category)
                .order_by(func.sum(Expense.amount_pkr).desc())
            )
        ).all()

        return BusinessStatsResponse(
            date_from=date_from,
            date_to=date_to,
            summary=ReportSummary(
                order_count=int(order_count),
                recognized_order_count=int(recognized_count),
                unique_customer_count=int(unique_customers),
                gross_sales_pkr=gross_sales_pkr,
                refunds_pkr=refunds_pkr,
                net_sales_pkr=net_sales_pkr,
                cogs_pkr=cogs_pkr,
                gross_profit_pkr=gross_profit_pkr,
                expenses_pkr=expenses_pkr,
                waste_cost_pkr=waste_cost_pkr,
                estimated_operating_result_pkr=gross_profit_pkr
                - expenses_pkr
                - waste_cost_pkr,
                received_purchase_cost_pkr=_money(received_purchase_total),
                draft_purchase_cost_pkr=_money(draft_purchase_total),
                average_order_value_pkr=(
                    round(net_sales_pkr / int(recognized_count)) if recognized_count else 0
                ),
                delivery_success_rate=(
                    round((int(success_count) / int(attempted_count)) * 100, 1)
                    if attempted_count
                    else None
                ),
                current_inventory_value_pkr=_money(inventory_value),
                current_low_stock_count=int(low_stock_count or 0),
            ),
            order_statuses=[
                StatusTotal(status=status, count=counts_by_status.get(status.value, 0))
                for status in OrderStatus
            ],
            daily=daily,
            top_products=[
                TopProductTotal(
                    product_id=product_id,
                    product_name=product_name,
                    unit_label=unit_label,
                    quantity=quantity,
                    revenue_pkr=_money(revenue),
                )
                for product_id, product_name, unit_label, quantity, revenue in top_rows
            ],
            expense_categories=[
                ExpenseCategoryTotal(category=category, amount_pkr=_money(amount))
                for category, amount in expense_rows
            ],
            accounting_note=(
                "Sales are based on the current status of orders created in the selected range. "
                "Estimated operating result subtracts FIFO COGS, active expenses, and waste; "
                "purchase spend is shown separately to avoid counting inventory cost twice."
            ),
        )
