from typing import Literal

from pydantic import Field

from app.schemas.common import APIModel

ImageContentType = Literal["image/jpeg", "image/png", "image/webp", "image/gif"]


class ProductImageUploadRequest(APIModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_type: ImageContentType
    size_bytes: int = Field(gt=0, le=10 * 1024 * 1024)


class ProductImageUploadResponse(APIModel):
    upload_url: str
    image_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str]
    expires_in: int
