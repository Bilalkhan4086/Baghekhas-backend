from fastapi import APIRouter, Response, status
from sqlalchemy import func, select

from app.dependencies import CurrentAdmin, SessionDep, SettingsDep
from app.exceptions import DomainError
from app.models import AdminUser
from app.schemas.auth import AdminResponse, LoginRequest, RefreshTokenRequest, TokenResponse
from app.security import verify_password
from app.services.auth import issue_token_pair, revoke_refresh_token, rotate_refresh_token

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    admin = await session.scalar(
        select(AdminUser).where(func.lower(AdminUser.email) == payload.email.lower())
    )
    if admin is None or not verify_password(payload.password, admin.password_hash):
        raise DomainError(401, "invalid_credentials", "Email or password is incorrect")
    if not admin.is_active:
        raise DomainError(403, "admin_inactive", "Administrator account is inactive")
    return await issue_token_pair(session, admin, settings)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    payload: RefreshTokenRequest,
    session: SessionDep,
    settings: SettingsDep,
) -> TokenResponse:
    return await rotate_refresh_token(session, payload.refresh_token, settings)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshTokenRequest, session: SessionDep) -> Response:
    await revoke_refresh_token(session, payload.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=AdminResponse)
async def me(admin: CurrentAdmin) -> AdminUser:
    return admin
