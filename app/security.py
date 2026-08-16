import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from secrets import token_urlsafe
from typing import Any

import jwt
from jwt import InvalidTokenError
from pwdlib import PasswordHash

from app.config import Settings

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Administrator passwords must contain at least 12 characters")
    return password_hash.hash(password)


def verify_password(password: str, encoded_hash: str) -> bool:
    return password_hash.verify(password, encoded_hash)


def create_access_token(admin_id: uuid.UUID, settings: Settings) -> tuple[str, int]:
    expires_in = settings.jwt_access_token_minutes * 60
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(admin_id),
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": str(uuid.uuid4()),
        "typ": "access",
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def create_rider_access_token(
    rider_id: uuid.UUID, auth_version: int, settings: Settings
) -> tuple[str, int]:
    expires_in = settings.rider_jwt_access_token_minutes * 60
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(rider_id),
        "iss": settings.jwt_issuer,
        "aud": settings.rider_jwt_audience,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in),
        "jti": str(uuid.uuid4()),
        "typ": "rider_access",
        "ver": auth_version,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, expires_in


def decode_access_token(token: str, settings: Settings) -> uuid.UUID:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
        subject = payload.get("sub")
        if not isinstance(subject, str):
            raise InvalidTokenError("Token subject is missing")
        token_type = payload.get("typ")
        if token_type not in (None, "access"):
            raise InvalidTokenError("Token type is invalid")
        return uuid.UUID(subject)
    except (InvalidTokenError, ValueError) as error:
        raise ValueError("Invalid or expired access token") from error


def decode_rider_access_token(token: str, settings: Settings) -> tuple[uuid.UUID, int]:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            audience=settings.rider_jwt_audience,
            issuer=settings.jwt_issuer,
        )
        subject = payload.get("sub")
        token_type = payload.get("typ")
        auth_version = payload.get("ver")
        if not isinstance(subject, str) or token_type != "rider_access":
            raise InvalidTokenError("Rider token claims are invalid")
        if not isinstance(auth_version, int) or isinstance(auth_version, bool) or auth_version < 0:
            raise InvalidTokenError("Rider token version is invalid")
        return uuid.UUID(subject), auth_version
    except (InvalidTokenError, ValueError) as error:
        raise ValueError("Invalid or expired rider access token") from error


def create_refresh_token() -> tuple[str, str]:
    token = token_urlsafe(48)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return sha256(token.encode()).hexdigest()
