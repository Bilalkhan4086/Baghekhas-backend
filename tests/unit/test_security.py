import uuid

import pytest

from app.config import Settings
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)


def settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql://test:test@localhost:5432/bagh_test",
        JWT_SECRET="unit-test-secret-that-is-at-least-32-characters",
    )


def test_password_hash_round_trip() -> None:
    encoded = hash_password("a-strong-admin-password")
    assert verify_password("a-strong-admin-password", encoded)
    assert not verify_password("wrong-password", encoded)


def test_short_password_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("short")


def test_access_token_round_trip() -> None:
    admin_id = uuid.uuid4()
    token, expires_in = create_access_token(admin_id, settings())
    assert expires_in == 1800
    assert decode_access_token(token, settings()) == admin_id


def test_access_token_rejects_wrong_secret() -> None:
    admin_id = uuid.uuid4()
    token, _ = create_access_token(admin_id, settings())
    other = Settings(
        DATABASE_URL="postgresql://test:test@localhost:5432/bagh_test",
        JWT_SECRET="different-test-secret-that-is-at-least-32-chars",
    )
    with pytest.raises(ValueError, match="Invalid or expired"):
        decode_access_token(token, other)


def test_refresh_tokens_are_random_and_hashable() -> None:
    first, first_hash = create_refresh_token()
    second, second_hash = create_refresh_token()
    assert first != second
    assert first_hash != second_hash
    assert hash_refresh_token(first) == first_hash
    assert len(first_hash) == 64
