from fastapi import APIRouter

from app.dependencies import CurrentAdmin, SettingsDep
from app.schemas.uploads import ProductImageUploadRequest, ProductImageUploadResponse
from app.services.storage import create_product_image_upload

router = APIRouter(prefix="/admin/uploads", tags=["admin uploads"])


@router.post("/product-images/presign", response_model=ProductImageUploadResponse)
async def presign_product_image(
    payload: ProductImageUploadRequest,
    _admin: CurrentAdmin,
    settings: SettingsDep,
) -> ProductImageUploadResponse:
    return create_product_image_upload(payload, settings)
