from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.request_guard import RequestGuardMiddleware


def test_sensitive_route_rate_limit_uses_stable_error() -> None:
    guarded = FastAPI()
    guarded.add_middleware(
        RequestGuardMiddleware,
        max_body_bytes=1024,
        login_requests_per_minute=2,
        public_order_requests_per_minute=2,
    )

    @guarded.post("/api/v1/auth/login")
    async def login() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(guarded) as client:
        assert client.post("/api/v1/auth/login").status_code == 200
        assert client.post("/api/v1/auth/login").status_code == 200
        limited = client.post("/api/v1/auth/login")

    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert limited.json()["detail"]["code"] == "rate_limit_exceeded"
