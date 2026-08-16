from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import api_router
from app.config import get_settings
from app.database import dispose_engine
from app.exceptions import DomainError
from app.request_guard import RequestGuardMiddleware
from app.routers.health import router as health_router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


settings = get_settings()
CORS_ALLOWED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")
app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    lifespan=lifespan,
)
app.add_middleware(
    RequestGuardMiddleware,
    max_body_bytes=settings.max_request_body_bytes,
    login_requests_per_minute=settings.login_requests_per_minute,
    public_order_requests_per_minute=settings.public_order_requests_per_minute,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )


@app.exception_handler(DomainError)
async def handle_domain_error(_request: Request, error: DomainError) -> JSONResponse:
    detail: dict[str, Any] = {"code": error.code, "message": error.message}
    if error.fields:
        detail["fields"] = error.fields
    return JSONResponse(status_code=error.status_code, content={"detail": detail})


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, error: RequestValidationError) -> JSONResponse:
    fields = [
        {
            "path": ".".join(str(part) for part in item["loc"] if part != "body"),
            "message": item["msg"],
            "type": item["type"],
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": "validation_error",
                "message": "Request validation failed",
                "fields": fields,
            }
        },
    )


app.include_router(health_router)
app.include_router(api_router)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"name": settings.app_name, "docs": "/docs"}
