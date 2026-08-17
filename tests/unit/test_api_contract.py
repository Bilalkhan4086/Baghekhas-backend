import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import CORS_ALLOWED_METHODS, app


def test_liveness_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_contains_public_and_admin_routes() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/orders" in paths
    assert "/api/v1/catalog/products" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/auth/refresh" in paths
    assert "/api/v1/auth/logout" in paths
    assert "/api/v1/admin/uploads/product-images/presign" in paths
    assert "/api/v1/admin/products/{product_id}/inventory-adjustments" in paths
    assert "/api/v1/admin/orders/{order_id}" in paths
    assert "/api/v1/admin/orders/delivery-settings" in paths
    assert "/api/v1/admin/orders/delivery-quote" in paths
    assert "post" in paths["/api/v1/admin/orders"]
    assert "/api/v1/admin/purchases" in paths
    assert "/api/v1/admin/purchases/{purchase_id}/receive" in paths
    assert {"get", "put", "delete"}.issubset(
        paths["/api/v1/admin/purchases/{purchase_id}"]
    )
    assert "/api/v1/admin/products/{product_id}/inventory" in paths
    assert "/api/v1/admin/products/{product_id}/batches" in paths
    assert "/api/v1/admin/products/{product_id}/movements" in paths
    assert "/api/v1/admin/inventory/waste" in paths
    assert "/api/v1/admin/inventory/adjustments" in paths
    assert "/api/v1/admin/inventory/procurement" in paths
    assert "/api/v1/admin/orders/{order_id}/confirm" in paths
    assert "/api/v1/admin/orders/{order_id}/start-packing" in paths
    assert "/api/v1/admin/orders/{order_id}/dispatch" in paths
    assert "/api/v1/admin/orders/{order_id}/reassign-rider" in paths
    assert "/api/v1/admin/orders/{order_id}/available-actions" in paths
    assert "/api/v1/admin/zones" in paths
    assert "/api/v1/admin/riders" in paths
    assert "/api/v1/rider/auth/login" in paths
    assert "/api/v1/rider/me/deliveries/today" in paths
    assert "/api/v1/rider/orders/{order_id}/delivered" in paths
    assert "/api/v1/catalog/delivery-preview" in paths
    assert "/api/v1/admin/customers" in paths
    assert "/api/v1/admin/customers/{customer_phone}" in paths


def test_delivery_charge_override_is_admin_only() -> None:
    schema = app.openapi()
    paths = schema["paths"]
    public_body = paths["/api/v1/orders"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]
    admin_body = paths["/api/v1/admin/orders"]["post"]["requestBody"]["content"][
        "application/json"
    ]["schema"]

    assert public_body["$ref"].endswith("/OrderCreate")
    assert admin_body["$ref"].endswith("/AdminOrderCreate")
    assert "delivery_charge_pkr" not in schema["components"]["schemas"]["OrderCreate"][
        "properties"
    ]
    assert "delivery_charge_pkr" in schema["components"]["schemas"]["AdminOrderCreate"][
        "properties"
    ]


def test_public_catalog_schema_excludes_operations_fields() -> None:
    schema = app.openapi()
    fields = schema["components"]["schemas"]["CatalogProductResponse"]["properties"]
    assert {"is_available", "availability"}.issubset(fields)
    assert not {"stock_quantity", "inventory_mode", "manual_available", "cogs_pkr"}.intersection(
        fields
    )


def test_validation_errors_use_consistent_shape() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/orders",
            headers={"Idempotency-Key": "not-a-uuid"},
            json={},
        )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "validation_error"
    assert isinstance(detail["fields"], list)


def test_request_body_limit_uses_stable_error_shape() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/orders",
            content=b"x" * 1_048_577,
            headers={"Content-Type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "request_too_large"


def test_cors_preflight_allows_delete_requests() -> None:
    cors_app = FastAPI()
    cors_app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173"],
        allow_methods=CORS_ALLOWED_METHODS,
        allow_headers=["Authorization", "Content-Type"],
    )
    with TestClient(cors_app) as client:
        response = client.options(
            "/api/v1/admin/purchases/00000000-0000-0000-0000-000000000001",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "authorization",
            },
        )

    assert response.status_code == 200
    assert "DELETE" in response.headers["access-control-allow-methods"]


@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        (
            "POST",
            "/api/v1/admin/purchases",
            {"supplier": "Mandi", "purchase_date": "2026-08-14", "items": []},
        ),
        (
            "PUT",
            "/api/v1/admin/purchases/00000000-0000-0000-0000-000000000001",
            {"supplier": "Mandi", "purchase_date": "2026-08-14", "items": []},
        ),
        (
            "DELETE",
            "/api/v1/admin/purchases/00000000-0000-0000-0000-000000000001",
            None,
        ),
        (
            "POST",
            "/api/v1/admin/purchases/00000000-0000-0000-0000-000000000001/receive",
            None,
        ),
        (
            "POST",
            "/api/v1/admin/purchases/00000000-0000-0000-0000-000000000001/cancel",
            None,
        ),
        (
            "POST",
            "/api/v1/admin/inventory/waste",
            {"product_id": "mango", "quantity": "1", "reason": "rotten"},
        ),
        (
            "POST",
            "/api/v1/admin/inventory/adjustments",
            {"product_id": "mango", "delta": "1", "reason": "restock"},
        ),
    ],
)
def test_inventory_writes_require_administrator_authentication(
    method: str, path: str, payload: dict[str, object] | None
) -> None:
    with TestClient(app) as client:
        response = client.request(method, path, json=payload)
    assert response.status_code == 401
