from datetime import date

from pydantic import Field

from app.enums import ExpenseCategory, OrderStatus
from app.schemas.common import APIModel
from app.schemas.products import Quantity


class ReportSummary(APIModel):
    order_count: int = Field(ge=0)
    recognized_order_count: int = Field(ge=0)
    unique_customer_count: int = Field(ge=0)
    gross_sales_pkr: int
    refunds_pkr: int
    net_sales_pkr: int
    cogs_pkr: int
    gross_profit_pkr: int
    expenses_pkr: int
    waste_cost_pkr: int
    estimated_operating_result_pkr: int
    received_purchase_cost_pkr: int
    draft_purchase_cost_pkr: int
    average_order_value_pkr: int
    delivery_success_rate: float | None = Field(default=None, ge=0, le=100)
    current_inventory_value_pkr: int
    current_low_stock_count: int = Field(ge=0)


class StatusTotal(APIModel):
    status: OrderStatus
    count: int = Field(ge=0)


class DailyBusinessTotal(APIModel):
    date: date
    order_count: int = Field(ge=0)
    net_sales_pkr: int
    expenses_pkr: int


class TopProductTotal(APIModel):
    product_id: str
    product_name: str
    unit_label: str | None
    quantity: Quantity
    revenue_pkr: int


class ExpenseCategoryTotal(APIModel):
    category: ExpenseCategory
    amount_pkr: int = Field(ge=0)


class BusinessStatsResponse(APIModel):
    date_from: date
    date_to: date
    summary: ReportSummary
    order_statuses: list[StatusTotal]
    daily: list[DailyBusinessTotal]
    top_products: list[TopProductTotal]
    expense_categories: list[ExpenseCategoryTotal]
    accounting_note: str
