from fastapi import APIRouter
from sqlalchemy import text

from app.dependencies import SessionDep
from app.exceptions import DomainError

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(session: SessionDep) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except Exception as error:
        raise DomainError(503, "database_unavailable", "Database is unavailable") from error
    return {"status": "ok"}
