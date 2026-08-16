from fastapi import APIRouter

from app.routers import (
    admin_customers,
    admin_delivery,
    admin_inventory,
    admin_orders,
    admin_products,
    admin_uploads,
    auth,
    catalog,
    orders,
    rider,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(catalog.router)
api_router.include_router(orders.router)
api_router.include_router(rider.router)
api_router.include_router(admin_uploads.router)
api_router.include_router(admin_products.router)
api_router.include_router(admin_inventory.router)
api_router.include_router(admin_delivery.router)
api_router.include_router(admin_customers.router)
api_router.include_router(admin_orders.router)
