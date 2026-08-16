from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.exceptions import DomainError
from app.models import AdminRefreshSession, AdminUser
from app.schemas.auth import TokenResponse
from app.security import create_access_token, create_refresh_token, hash_refresh_token


def invalid_refresh_token() -> DomainError:
    return DomainError(401, "invalid_refresh_token", "Refresh token is invalid or expired")


async def issue_token_pair(
    session: AsyncSession,
    admin: AdminUser,
    settings: Settings,
) -> TokenResponse:
    access_token, expires_in = create_access_token(admin.id, settings)
    refresh_token, token_hash = create_refresh_token()
    refresh_expires_in = settings.jwt_refresh_token_days * 24 * 60 * 60
    session.add(
        AdminRefreshSession(
            admin_id=admin.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC) + timedelta(seconds=refresh_expires_in),
        )
    )
    await session.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
    )


async def rotate_refresh_token(
    session: AsyncSession,
    raw_token: str,
    settings: Settings,
) -> TokenResponse:
    now = datetime.now(UTC)
    refresh_session = await session.scalar(
        select(AdminRefreshSession)
        .where(AdminRefreshSession.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if (
        refresh_session is None
        or refresh_session.revoked_at is not None
        or refresh_session.expires_at <= now
    ):
        raise invalid_refresh_token()

    admin = await session.get(AdminUser, refresh_session.admin_id)
    if admin is None or not admin.is_active:
        refresh_session.revoked_at = now
        await session.commit()
        raise invalid_refresh_token()

    refresh_session.revoked_at = now
    return await issue_token_pair(session, admin, settings)


async def revoke_refresh_token(session: AsyncSession, raw_token: str) -> None:
    refresh_session = await session.scalar(
        select(AdminRefreshSession)
        .where(AdminRefreshSession.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if refresh_session is not None and refresh_session.revoked_at is None:
        refresh_session.revoked_at = datetime.now(UTC)
        await session.commit()
