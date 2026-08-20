"""Small process-local request guards; production edge controls remain recommended."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import Message, Receive, Scope, Send

ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class RequestBodyTooLarge(Exception):
    pass


class RequestGuardMiddleware:
    """Enforce a body limit and baseline per-client limits on sensitive public writes."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_body_bytes: int,
        login_requests_per_minute: int,
        public_order_requests_per_minute: int,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.login_requests_per_minute = login_requests_per_minute
        self.public_order_requests_per_minute = public_order_requests_per_minute
        self._requests: dict[tuple[str, str], deque[float]] = {}
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = self._content_length(scope)
        if content_length is not None and content_length > self.max_body_bytes:
            await self._error(send, 413, "request_too_large", "Request body is too large")
            return

        limit_key = self._limit_key(scope)
        if limit_key is not None and not await self._allow(limit_key[0], limit_key[1]):
            await self._error(
                send,
                429,
                "rate_limit_exceeded",
                "Too many requests; retry later",
                headers={"Retry-After": "60"},
            )
            return

        received = 0

        async def guarded_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    raise RequestBodyTooLarge
            return message

        try:
            await self.app(scope, guarded_receive, send)
        except RequestBodyTooLarge:
            await self._error(send, 413, "request_too_large", "Request body is too large")

    @staticmethod
    def _content_length(scope: Scope) -> int | None:
        for name, value in scope.get("headers", []):
            if name.lower() == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    def _limit_key(self, scope: Scope) -> tuple[tuple[str, str], int] | None:
        if scope.get("method") != "POST":
            return None
        path = str(scope.get("path", ""))
        if path in {"/api/v1/auth/login", "/api/v1/rider/auth/login"}:
            bucket = "login"
            limit = self.login_requests_per_minute
        elif path in {"/api/v1/orders", "/api/v1/orders/track"}:
            bucket = "public-order" if path == "/api/v1/orders" else "order-tracking"
            limit = self.public_order_requests_per_minute
        else:
            return None
        client = scope.get("client")
        client_host = str(client[0]) if client else "unknown"
        return (bucket, client_host), limit

    async def _allow(self, key: tuple[str, str], limit: int) -> bool:
        now = monotonic()
        cutoff = now - 60
        async with self._lock:
            requests = self._requests.setdefault(key, deque())
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= limit:
                return False
            requests.append(now)
            if len(self._requests) > 10_000:
                self._requests = {
                    bucket_key: values
                    for bucket_key, values in self._requests.items()
                    if values and values[-1] > cutoff
                }
            return True

    @staticmethod
    async def _error(
        send: Send,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={"detail": {"code": code, "message": message}},
            headers=headers,
        )
        await response({"type": "http"}, _empty_receive, send)


async def _empty_receive() -> Any:
    return {"type": "http.request", "body": b"", "more_body": False}
