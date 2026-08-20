from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies import CurrentAdmin, SessionDep
from app.exceptions import DomainError
from app.schemas.reporting import BusinessStatsResponse
from app.services.reporting import ReportingService

router = APIRouter(prefix="/admin/reports", tags=["admin reporting"])


@router.get("/business", response_model=BusinessStatsResponse)
async def business_report(
    session: SessionDep,
    _admin: CurrentAdmin,
    date_from: Annotated[date, Query()],
    date_to: Annotated[date, Query()],
) -> BusinessStatsResponse:
    if date_from > date_to:
        raise DomainError(422, "invalid_date_range", "From date cannot be after to date")
    if date_to - date_from > timedelta(days=365):
        raise DomainError(422, "report_range_too_large", "Reports are limited to 366 days")
    return await ReportingService(session).business_stats(date_from, date_to)
