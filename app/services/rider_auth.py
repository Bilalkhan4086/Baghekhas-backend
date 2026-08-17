"""Rotating, hashed refresh sessions for the separate rider token audience."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.exceptions import DomainError
from app.models import Rider, RiderRefreshSession
from app.schemas.rider import RiderIdentityResponse, RiderTokenResponse
from app.security import create_refresh_token, create_rider_access_token, hash_refresh_token


def invalid_rider_refresh_token() -> DomainError:
    return DomainError(
        401,
        "invalid_rider_refresh_token",
        "Rider refresh token is invalid or expired",
    )


def _new_pair(
    session: AsyncSession,
    rider: Rider,
    settings: Settings,
) -> RiderTokenResponse:
    access_token, expires_in = create_rider_access_token(
        rider.id, rider.auth_version, settings
    )
    refresh_token, token_hash = create_refresh_token()
    refresh_expires_in = settings.rider_refresh_token_days * 24 * 60 * 60
    session.add(
        RiderRefreshSession(
            rider_id=rider.id,
            token_hash=token_hash,
            auth_version=rider.auth_version,
            expires_at=datetime.now(UTC) + timedelta(seconds=refresh_expires_in),
        )
    )
    return RiderTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        refresh_expires_in=refresh_expires_in,
        rider=RiderIdentityResponse(id=rider.id, name=rider.name),
    )


async def issue_rider_token_pair(
    session: AsyncSession,
    rider: Rider,
    settings: Settings,
) -> RiderTokenResponse:
    response = _new_pair(session, rider, settings)
    await session.commit()
    return response


async def rotate_rider_refresh_token(
    session: AsyncSession,
    raw_token: str,
    settings: Settings,
) -> RiderTokenResponse:
    now = datetime.now(UTC)
    refresh_session = await session.scalar(
        select(RiderRefreshSession)
        .where(RiderRefreshSession.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if (
        refresh_session is None
        or refresh_session.revoked_at is not None
        or refresh_session.expires_at <= now
    ):
        raise invalid_rider_refresh_token()

    rider = await session.get(Rider, refresh_session.rider_id)
    if (
        rider is None
        or not rider.is_active
        or rider.auth_version != refresh_session.auth_version
    ):
        refresh_session.revoked_at = now
        await session.commit()
        raise invalid_rider_refresh_token()

    refresh_session.revoked_at = now
    response = _new_pair(session, rider, settings)
    await session.commit()
    return response


async def revoke_rider_refresh_token(session: AsyncSession, raw_token: str) -> None:
    refresh_session = await session.scalar(
        select(RiderRefreshSession)
        .where(RiderRefreshSession.token_hash == hash_refresh_token(raw_token))
        .with_for_update()
    )
    if refresh_session is not None and refresh_session.revoked_at is None:
        refresh_session.revoked_at = datetime.now(UTC)
        await session.commit()
