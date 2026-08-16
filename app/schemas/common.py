from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


T = TypeVar("T")


class Page(APIModel, Generic[T]):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class ErrorDetail(APIModel):
    code: str
    message: str
    fields: list[dict[str, Any]] | None = None


class ErrorResponse(APIModel):
    detail: ErrorDetail
