from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.database import get_session
from app.exceptions import DomainError
from app.models import AdminUser, Rider
from app.security import decode_access_token, decode_rider_access_token

bearer_scheme = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


async def get_current_admin(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
    settings: SettingsDep,
) -> AdminUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise DomainError(401, "invalid_token", "Bearer access token is required")
    try:
        admin_id = decode_access_token(credentials.credentials, settings)
    except ValueError as error:
        raise DomainError(401, "invalid_token", str(error)) from error

    admin = await session.get(AdminUser, admin_id)
    if admin is None or not admin.is_active:
        raise DomainError(401, "invalid_token", "Administrator account is unavailable")
    return admin


CurrentAdmin = Annotated[AdminUser, Depends(get_current_admin)]


async def get_current_rider(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
    settings: SettingsDep,
) -> Rider:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise DomainError(401, "invalid_rider_token", "Bearer rider access token is required")
    try:
        rider_id, auth_version = decode_rider_access_token(credentials.credentials, settings)
    except ValueError as error:
        raise DomainError(401, "invalid_rider_token", str(error)) from error

    rider = await session.get(Rider, rider_id)
    if rider is None or not rider.is_active or rider.auth_version != auth_version:
        raise DomainError(401, "invalid_rider_token", "Rider account is unavailable")
    return rider


CurrentRider = Annotated[Rider, Depends(get_current_rider)]
