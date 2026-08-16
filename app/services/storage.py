import uuid
from typing import Any
from urllib.parse import quote

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]
from botocore.exceptions import BotoCoreError, ClientError  # type: ignore[import-untyped]

from app.config import Settings
from app.exceptions import DomainError
from app.schemas.uploads import ProductImageUploadRequest, ProductImageUploadResponse

CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def create_product_image_upload(
    payload: ProductImageUploadRequest,
    settings: Settings,
    *,
    s3_client: Any | None = None,
) -> ProductImageUploadResponse:
    object_key = f"products/{uuid.uuid4().hex}{CONTENT_TYPE_EXTENSIONS[payload.content_type]}"
    client = s3_client or boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id.get_secret_value(),
        aws_secret_access_key=settings.aws_secret_access_key.get_secret_value(),
        region_name=settings.s3direct_region,
        endpoint_url=f"https://s3.{settings.s3direct_region}.amazonaws.com",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "virtual"},
        ),
    )

    try:
        upload_url = client.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": settings.aws_storage_bucket_name,
                "Key": object_key,
                "ContentType": payload.content_type,
            },
            ExpiresIn=settings.s3_presigned_url_expiration_seconds,
            HttpMethod="PUT",
        )
    except (BotoCoreError, ClientError) as error:
        raise DomainError(
            503,
            "storage_unavailable",
            "The image upload service is temporarily unavailable",
        ) from error

    return ProductImageUploadResponse(
        upload_url=upload_url,
        image_url=f"{settings.aws_s3_url}/{quote(object_key)}",
        headers={"Content-Type": payload.content_type},
        expires_in=settings.s3_presigned_url_expiration_seconds,
    )
