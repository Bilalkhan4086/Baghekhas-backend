"""Destructive integration tests for an explicitly disposable PostgreSQL database."""

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import Settings
from app.enums import OrderStatus
from app.exceptions import DomainError
from app.models import (
    AdminRefreshSession,
    AdminUser,
    CustomerAddress,
    InventoryMovement,
    Product,
    Rider,
)
from app.schemas.orders import (
    CustomerInput,
    DeliveryLocationInput,
    OrderAdminUpdate,
    OrderCreate,
    OrderItemInput,
)
from app.schemas.products import InventoryAdjustmentCreate
from app.services.auth import issue_token_pair, revoke_refresh_token, rotate_refresh_token
from app.services.order_transitions import OrderTransitionService
from app.services.orders import create_order, update_order
from app.services.products import adjust_inventory

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ALLOW_RESET = os.getenv("ALLOW_DATABASE_RESET") == "true"
pytestmark = pytest.mark.integration


def async_url(url: str) -> str:
    if url.startswith("postgresql+psycopg://"):
        return url
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(autouse=True)
def require_disposable_database() -> None:
    if not TEST_DATABASE_URL or not ALLOW_RESET:
        pytest.skip("Set TEST_DATABASE_URL and ALLOW_DATABASE_RESET=true for integration tests")


async def reset_database() -> None:
    assert TEST_DATABASE_URL is not None
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    async with engine.begin() as connection:
        await connection.execute(text("DROP SCHEMA public CASCADE"))
        await connection.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


def run_migrations() -> None:
    assert TEST_DATABASE_URL is not None
    backend_root = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "JWT_SECRET": "integration-test-secret-that-is-at-least-32-characters",
    }
    environment.pop("NEON_DB_CONNECTION", None)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_root,
        env=environment,
        check=True,
    )


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    assert TEST_DATABASE_URL is not None
    backend_root = Path(__file__).resolve().parents[2]
    environment = {
        **os.environ,
        "DATABASE_URL": TEST_DATABASE_URL,
        "JWT_SECRET": "integration-test-secret-that-is-at-least-32-characters",
    }
    environment.pop("NEON_DB_CONNECTION", None)
    return subprocess.run(
        [sys.executable, "-m", "app.cli", *arguments],
        cwd=backend_root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )


def token_settings() -> Settings:
    return Settings(
        DATABASE_URL="postgresql://test:test@localhost:5432/bagh_test",
        JWT_SECRET="integration-test-secret-that-is-at-least-32-characters",
    )


@pytest.mark.asyncio
async def test_migration_upgrades_legacy_schema_without_losing_orders() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    order_id = uuid.uuid4()
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE customers (
                  phone text PRIMARY KEY, name varchar(120) NOT NULL,
                  address varchar(500) NOT NULL, created_at timestamptz DEFAULT now() NOT NULL,
                  updated_at timestamptz DEFAULT now() NOT NULL
                );
                CREATE TABLE orders (
                  id uuid PRIMARY KEY, customer_phone text NOT NULL REFERENCES customers(phone),
                  notes varchar(500), status varchar(40) DEFAULT 'pending_whatsapp' NOT NULL,
                  total_pkr integer NOT NULL, user_agent varchar(500),
                  created_at timestamptz DEFAULT now() NOT NULL
                );
                CREATE TABLE order_items (
                  id uuid PRIMARY KEY,
                  order_id uuid NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                  product_id varchar(120) NOT NULL, product_name varchar(200) NOT NULL,
                  unit_price_pkr integer NOT NULL, quantity integer NOT NULL,
                  line_total_pkr integer NOT NULL, UNIQUE(order_id, product_id)
                )
                """
            )
        )
        await connection.execute(
            text(
                "INSERT INTO customers(phone,name,address) VALUES "
                "('+923001234567','Ayesha','Lahore')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO orders(id,customer_phone,total_pkr) VALUES (:id,'+923001234567',500)"
            ),
            {"id": order_id},
        )
        await connection.execute(
            text(
                "INSERT INTO order_items(id,order_id,product_id,product_name,"
                "unit_price_pkr,quantity,line_total_pkr) "
                "VALUES (:item_id,:order_id,'mango','Mango',500,1,500)"
            ),
            {"item_id": uuid.uuid4(), "order_id": order_id},
        )
    await engine.dispose()

    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    legacy_order_after_migration_id = uuid.uuid4()
    async with engine.begin() as connection:
        status = await connection.scalar(
            text("SELECT status FROM orders WHERE id=:id"), {"id": order_id}
        )
        quantity = await connection.scalar(
            text("SELECT quantity FROM order_items WHERE order_id=:id"), {"id": order_id}
        )
        history_count = await connection.scalar(
            text("SELECT count(*) FROM order_status_history WHERE order_id=:id"), {"id": order_id}
        )
        pricing = (
            await connection.execute(
                text(
                    "SELECT subtotal_pkr, delivery_charge_pkr, delivery_latitude "
                    "FROM orders WHERE id=:id"
                ),
                {"id": order_id},
            )
        ).one()
        await connection.execute(
            text(
                "INSERT INTO orders(id,customer_phone,total_pkr) VALUES (:id,'+923001234567',750)"
            ),
            {"id": legacy_order_after_migration_id},
        )
        later_legacy_pricing = (
            await connection.execute(
                text("SELECT subtotal_pkr, delivery_charge_pkr FROM orders WHERE id=:id"),
                {"id": legacy_order_after_migration_id},
            )
        ).one()
    await engine.dispose()
    assert status == "pending"
    assert quantity == Decimal("1.000")
    assert history_count == 1
    assert pricing.subtotal_pkr == 500
    assert pricing.delivery_charge_pkr == 0
    assert pricing.delivery_latitude is None
    assert later_legacy_pricing.subtotal_pkr == 750
    assert later_legacy_pricing.delivery_charge_pkr == 0


@pytest.mark.asyncio
async def test_refresh_session_is_rotated_and_revoked() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = AdminUser(
        id=uuid.uuid4(),
        email="refresh-admin@example.com",
        password_hash="not-used-in-this-test",
    )
    async with factory() as session:
        session.add(admin)
        await session.commit()
        first = await issue_token_pair(session, admin, token_settings())
        second = await rotate_refresh_token(session, first.refresh_token, token_settings())
        assert second.access_token != first.access_token
        assert second.refresh_token != first.refresh_token
        with pytest.raises(DomainError, match="invalid or expired"):
            await rotate_refresh_token(session, first.refresh_token, token_settings())
        await revoke_refresh_token(session, second.refresh_token)
        with pytest.raises(DomainError, match="invalid or expired"):
            await rotate_refresh_token(session, second.refresh_token, token_settings())
        session_count = await session.scalar(
            select(text("count(*)")).select_from(AdminRefreshSession)
        )

    await engine.dispose()
    assert session_count == 2


@pytest.mark.asyncio
async def test_concurrent_adjustments_are_serialized() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            AdminUser(
                id=admin_id,
                email="admin@example.com",
                password_hash="not-used-in-this-test",
            )
        )
        session.add(
            Product(
                id="mango",
                name="Mango",
                description="Fresh mangoes",
                image_url="/mango.png",
                category="fruit",
                catalog_type="product",
                unit_label="kg",
                base_price_pkr=500,
                pricing_type="fixed",
                publication_status="active",
                inventory_mode="tracked",
                manual_available=True,
                stock_quantity=Decimal("10"),
                low_stock_threshold=Decimal("2"),
            )
        )
        await session.commit()

    async def apply(delta: str) -> None:
        async with factory() as session:
            admin = await session.get(AdminUser, admin_id)
            assert admin is not None
            await adjust_inventory(
                session,
                "mango",
                InventoryAdjustmentCreate(delta=delta, reason="correction"),
                admin,
            )

    await asyncio.gather(apply("1.250"), apply("-0.500"))
    async with factory() as session:
        product = await session.get(Product, "mango")
        movement_count = await session.scalar(
            select(text("count(*)")).select_from(InventoryMovement)
        )
    await engine.dispose()
    assert product is not None
    assert product.stock_quantity == Decimal("10.750")
    assert movement_count == 2


@pytest.mark.asyncio
async def test_catalog_seed_is_idempotent() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    first = await asyncio.to_thread(run_cli, "seed-catalog")
    second = await asyncio.to_thread(run_cli, "seed-catalog")

    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    async with engine.connect() as connection:
        count = await connection.scalar(text("SELECT count(*) FROM products"))
    await engine.dispose()
    assert count == 54
    assert "54 inserted" in first.stdout
    assert "0 inserted, 54 skipped" in second.stdout


@pytest.mark.asyncio
async def test_order_is_idempotent_and_never_changes_stock() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = AdminUser(
        id=uuid.uuid4(),
        email="orders-admin@example.com",
        password_hash="not-used-in-this-test",
    )
    product = Product(
        id="mango",
        name="Mango",
        description="Fresh mangoes",
        image_url="/mango.png",
        category="fruit",
        catalog_type="product",
        unit_label="kg",
        base_price_pkr=500,
        pricing_type="fixed",
        publication_status="active",
        inventory_mode="tracked",
        manual_available=True,
        stock_quantity=Decimal("10"),
        low_stock_threshold=Decimal("2"),
    )
    async with factory() as session:
        rider = Rider(
            id=uuid.uuid4(),
            name="Rider",
            phone="03009999999",
            password_hash=None,
            is_active=True,
        )
        session.add_all([admin, product, rider])
        await session.commit()

        payload = OrderCreate(
            customer=CustomerInput(
                name="Ayesha",
                phone="03001234567",
                address="Lahore",
            ),
            delivery_location=DeliveryLocationInput(
                latitude="31.469437737970402",
                longitude="74.27264727389156",
            ),
            items=[OrderItemInput(product_id="mango", quantity="1.250")],
        )
        key = uuid.uuid4()
        created_order, created = await create_order(
            session, payload, idempotency_key=key, user_agent="pytest"
        )
        retried_order, retry_created = await create_order(
            session, payload, idempotency_key=key, user_agent="pytest"
        )
        assert created is True
        assert retry_created is False
        assert retried_order.id == created_order.id
        assert created_order.total_pkr == 625
        assert created_order.subtotal_pkr == 625
        assert created_order.delivery_charge_pkr == 0
        assert created_order.delivery_distance_km == Decimal("0.000")
        assert created_order.customer_name_snapshot == "Ayesha"
        assert created_order.delivery_address_snapshot == "Lahore"
        assert created_order.promised_delivery_date is not None
        assert created_order.promised_delivery_time == time(18, 0)

        overridden_order, overridden_created = await create_order(
            session,
            payload,
            idempotency_key=uuid.uuid4(),
            user_agent="pytest",
            delivery_charge_override_pkr=75,
        )
        assert overridden_created is True
        assert overridden_order.subtotal_pkr == 625
        assert overridden_order.delivery_charge_pkr == 75
        assert overridden_order.total_pkr == 700

        rescheduled = await update_order(
            session,
            created_order.id,
            OrderAdminUpdate(
                promised_delivery_date=date.today() + timedelta(days=2),
                promised_delivery_time=time(19, 30),
            ),
            admin,
        )
        assert rescheduled.promised_delivery_time == time(19, 30)

        changed_customer_payload = payload.model_copy(
            update={
                "customer": CustomerInput(
                    name="Ayesha Updated",
                    phone="03001234567",
                    address="Karachi",
                )
            }
        )
        await create_order(
            session,
            changed_customer_payload,
            idempotency_key=uuid.uuid4(),
            user_agent="pytest",
        )
        await session.refresh(created_order)
        default_address_count = await session.scalar(
            select(func.count())
            .select_from(CustomerAddress)
            .where(
                CustomerAddress.customer_phone == created_order.customer_phone,
                CustomerAddress.is_default.is_(True),
            )
        )
        assert created_order.customer_name_snapshot == "Ayesha"
        assert created_order.delivery_address_snapshot == "Lahore"
        assert default_address_count == 1

        transitions = OrderTransitionService(session)
        confirmed = await transitions.confirm_order(created_order.id, admin.id)
        packed = await transitions.start_packing(confirmed.id, admin.id)
        dispatched = await transitions.dispatch_order(packed.id, rider.id, admin.id)
        delivered = await transitions.deliver_order(dispatched.id, admin.id)
        completed = await update_order(
            session,
            delivered.id,
            OrderAdminUpdate(status=OrderStatus.COMPLETED),
            admin,
        )
        with pytest.raises(DomainError, match="Refund amount cannot exceed"):
            await update_order(
                session,
                completed.id,
                OrderAdminUpdate(status=OrderStatus.REFUNDED, refund_amount_pkr=626),
                admin,
            )
        refunded = await update_order(
            session,
            completed.id,
            OrderAdminUpdate(status=OrderStatus.REFUNDED, refund_amount_pkr=300),
            admin,
        )
        unchanged_product = await session.get(Product, "mango")

    await engine.dispose()
    assert refunded.status == OrderStatus.REFUNDED.value
    assert refunded.refund_amount_pkr == 300
    assert len(refunded.status_history) == 8
    assert unchanged_product is not None
    assert unchanged_product.stock_quantity == Decimal("8.750")
