from typing import Any
from urllib.parse import urlparse

import pytest
from pydantic import ValidationError

from app.config import get_settings
from app.schemas.uploads import ProductImageUploadRequest
from app.services.storage import create_product_image_upload


class FakeS3Client:
    def __init__(self) -> None:
        self.call: dict[str, Any] | None = None

    def generate_presigned_url(self, operation: str, **kwargs: Any) -> str:
        self.call = {"operation": operation, **kwargs}
        return "https://signed-upload.example.com/object?signature=test"


def test_create_product_image_upload_signs_exact_content_type() -> None:
    client = FakeS3Client()
    payload = ProductImageUploadRequest(
        file_name="mango.png",
        content_type="image/png",
        size_bytes=1024,
    )

    result = create_product_image_upload(payload, get_settings(), s3_client=client)

    assert result.upload_url.startswith("https://signed-upload.example.com/")
    assert result.image_url.startswith("https://test-bucket.s3.example.com/products/")
    assert result.image_url.endswith(".png")
    assert result.headers == {"Content-Type": "image/png"}
    assert client.call is not None
    assert client.call["operation"] == "put_object"
    assert client.call["HttpMethod"] == "PUT"
    assert client.call["Params"]["Bucket"] == "test-bucket"
    assert client.call["Params"]["ContentType"] == "image/png"


def test_create_product_image_upload_uses_configured_regional_endpoint() -> None:
    payload = ProductImageUploadRequest(
        file_name="mango.png",
        content_type="image/png",
        size_bytes=1024,
    )
    settings = get_settings().model_copy(
        update={
            "aws_storage_bucket_name": "baghekhas-prod-uploads",
            "s3direct_region": "ap-southeast-1",
        }
    )

    result = create_product_image_upload(payload, settings)

    assert urlparse(result.upload_url).hostname == (
        "baghekhas-prod-uploads.s3.ap-southeast-1.amazonaws.com"
    )


def test_product_image_upload_rejects_unsupported_type_and_large_file() -> None:
    with pytest.raises(ValidationError):
        ProductImageUploadRequest(
            file_name="mango.svg",
            content_type="image/svg+xml",  # type: ignore[arg-type]
            size_bytes=1024,
        )

    with pytest.raises(ValidationError):
        ProductImageUploadRequest(
            file_name="mango.png",
            content_type="image/png",
            size_bytes=(10 * 1024 * 1024) + 1,
        )
