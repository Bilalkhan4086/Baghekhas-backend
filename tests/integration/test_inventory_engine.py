"""Destructive PostgreSQL coverage for the Milestone 3 inventory services."""

import asyncio
import os
import subprocess
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import get_session
from app.dependencies import get_current_admin
from app.enums import (
    CustomerSegment,
    InventoryReservationStatus,
    OrderStatus,
    PurchaseCostAllocationMethod,
    PurchaseStatus,
    WasteReason,
)
from app.exceptions import DomainError
from app.main import app
from app.models import (
    AdminUser,
    Customer,
    DeliveryZone,
    InventoryBatch,
    InventoryMovement,
    InventoryReservation,
    Order,
    OrderItem,
    Product,
    Rider,
    RiderZone,
)
from app.security import hash_password
from app.services.customers import customer_detail, list_customers
from app.services.delivery import (
    KARACHI,
    DeliveryScheduleService,
    DeliveryZoneService,
    RiderAssignmentService,
    RiderService,
)
from app.services.inventory import (
    CostingService,
    InventoryReadService,
    InventoryService,
    PurchaseCostDraft,
    PurchaseItemDraft,
    PurchaseService,
    ReservationService,
    WasteService,
)
from app.services.order_transitions import OrderTransitionService

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
        "JWT_SECRET": "inventory-integration-secret-that-is-at-least-32-characters",
    }
    environment.pop("NEON_DB_CONNECTION", None)
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_root,
        env=environment,
        check=True,
    )


def product(product_id: str) -> Product:
    return Product(
        id=product_id,
        name=product_id.title(),
        description=f"Fresh {product_id}",
        image_url=f"/{product_id}.png",
        category="fruit",
        catalog_type="product",
        unit_label="kg",
        base_price_pkr=500,
        pricing_type="fixed",
        publication_status="active",
        inventory_mode="tracked",
        manual_available=True,
        stock_quantity=Decimal("0"),
        low_stock_threshold=Decimal("0"),
    )


async def create_order(session, order_id: uuid.UUID) -> None:  # type: ignore[no-untyped-def]
    customer = Customer(
        phone=f"+92{order_id.hex[:10]}",
        name="Ayesha",
        address="Lahore",
    )
    order = Order(
        id=order_id,
        customer_phone=customer.phone,
        status="pending",
        subtotal_pkr=0,
        delivery_charge_pkr=0,
        total_pkr=0,
    )
    session.add_all([customer, order])
    await session.commit()


@pytest.mark.asyncio
async def test_purchase_inventory_costing_and_waste_services() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin_id = uuid.uuid4()

    async with factory() as session:
        session.add_all(
            [
                AdminUser(
                    id=admin_id,
                    email="inventory-admin@example.com",
                    password_hash="not-used-in-this-test",
                ),
                product("mango"),
                product("guava"),
            ]
        )
        await session.commit()

        purchases = PurchaseService(session)
        editable = await purchases.create_purchase(
            supplier="Original supplier",
            purchase_date=date(2026, 7, 30),
            items=[PurchaseItemDraft("mango", Decimal("1"), Decimal("100"))],
            additional_costs=[],
            created_by_id=admin_id,
            cost_allocation_method=PurchaseCostAllocationMethod.BY_WEIGHT,
        )
        editable_id = editable.id
        await purchases.update_purchase(
            editable_id,
            supplier="Updated supplier",
            purchase_date=date(2026, 7, 31),
            items=[PurchaseItemDraft("guava", Decimal("2.5"), Decimal("80"))],
            additional_costs=[PurchaseCostDraft("transport", Decimal("25"))],
            cost_allocation_method=PurchaseCostAllocationMethod.BY_PURCHASE_VALUE,
            notes="Updated before receipt",
        )
        editable_detail = await InventoryReadService(session).purchase_detail(editable_id)
        assert editable_detail.supplier == "Updated supplier"
        assert editable_detail.subtotal == Decimal("200.00")
        assert editable_detail.total_cost == Decimal("225.00")
        assert editable_detail.items[0].product_id == "guava"
        assert editable_detail.costs[0].amount == Decimal("25.00")
        await session.rollback()
        await purchases.delete_purchase(editable_id)
        with pytest.raises(DomainError, match="Purchase was not found"):
            await InventoryReadService(session).purchase_detail(editable_id)
        await session.rollback()

        cancelled = await purchases.create_purchase(
            supplier="Mandi supplier",
            purchase_date=date(2026, 7, 31),
            items=[PurchaseItemDraft("mango", Decimal("1"), Decimal("100"))],
            additional_costs=[],
            created_by_id=admin_id,
            cost_allocation_method=PurchaseCostAllocationMethod.BY_WEIGHT,
        )
        assert (
            await purchases.cancel_purchase(cancelled.id)
        ).status == PurchaseStatus.CANCELLED.value
        with pytest.raises(DomainError, match="Only draft purchases can be edited"):
            await purchases.update_purchase(
                cancelled.id,
                supplier="Mandi supplier",
                purchase_date=date(2026, 7, 31),
                items=[PurchaseItemDraft("mango", Decimal("1"), Decimal("100"))],
                additional_costs=[],
                cost_allocation_method=PurchaseCostAllocationMethod.BY_WEIGHT,
            )
        with pytest.raises(DomainError, match="Only draft purchases can be deleted"):
            await purchases.delete_purchase(cancelled.id)
        with pytest.raises(DomainError, match="Only draft purchases can be received"):
            await purchases.receive_purchase(cancelled.id)

        first = await purchases.create_purchase(
            supplier="Mandi supplier",
            purchase_date=date(2026, 8, 1),
            items=[
                PurchaseItemDraft("mango", Decimal("2"), Decimal("100")),
                PurchaseItemDraft("guava", Decimal("2"), Decimal("200")),
            ],
            additional_costs=[PurchaseCostDraft("transport", Decimal("60"))],
            created_by_id=admin_id,
            cost_allocation_method=PurchaseCostAllocationMethod.BY_PURCHASE_VALUE,
        )
        await purchases.receive_purchase(first.id)
        await purchases.receive_purchase(first.id)

        second = await purchases.create_purchase(
            supplier="Mandi supplier",
            purchase_date=date(2026, 8, 2),
            items=[PurchaseItemDraft("mango", Decimal("3"), Decimal("150"))],
            additional_costs=[],
            created_by_id=admin_id,
            cost_allocation_method=PurchaseCostAllocationMethod.BY_WEIGHT,
        )
        await purchases.receive_purchase(second.id)

        batches = list(
            (
                await session.scalars(
                    select(InventoryBatch)
                    .where(InventoryBatch.product_id == "mango")
                    .order_by(InventoryBatch.received_at, InventoryBatch.id)
                )
            ).all()
        )
        assert len(batches) == 2
        assert batches[0].effective_cost == Decimal("110.00")
        assert batches[1].effective_cost == Decimal("150.00")

        inventory = InventoryService(session)
        assert await inventory.get_available_stock("mango") == Decimal("5.000")
        assert await inventory.calculate_inventory_cost("mango") == Decimal("134")
        assert await CostingService(session).suggest_selling_price(
            "mango", Decimal("25")
        ) == Decimal("178.6666666666666666666666667")
        await session.rollback()

        waste = await WasteService(session).record_waste(
            product_id="mango",
            quantity=Decimal("2.500"),
            reason=WasteReason.ROTTEN,
            notes="Spoiled in transit",
            admin_id=admin_id,
        )
        assert waste.cost == Decimal("295.00")
        assert [record.quantity for record in waste.records] == [Decimal("2.000"), Decimal("0.500")]
        assert await inventory.get_available_stock("mango") == Decimal("2.500")
        await session.rollback()

        adjustment = await WasteService(session).record_adjustment(
            product_id="mango",
            quantity_delta=Decimal("1.000"),
            reason="count correction",
            notes=None,
            admin_id=admin_id,
        )
        assert adjustment.quantity_delta == Decimal("1.000")
        assert await inventory.get_available_stock("mango") == Decimal("3.500")
        await session.rollback()
        with pytest.raises(DomainError, match="reason is required"):
            await WasteService(session).record_adjustment(
                product_id="mango",
                quantity_delta=Decimal("1"),
                reason=" ",
                notes=None,
                admin_id=admin_id,
            )
        with pytest.raises(DomainError, match="exceeds physical stock"):
            await WasteService(session).record_adjustment(
                product_id="mango",
                quantity_delta=Decimal("-99"),
                reason="count correction",
                notes=None,
                admin_id=admin_id,
            )

        movement_count = await session.scalar(select(func.count()).select_from(InventoryMovement))
        assert movement_count == 6

    await engine.dispose()


@pytest.mark.asyncio
async def test_fifo_reservation_release_sale_and_group_cogs() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin_id = uuid.uuid4()
    order_id = uuid.uuid4()

    async with factory() as session:
        session.add_all(
            [
                AdminUser(
                    id=admin_id,
                    email="reservation-admin@example.com",
                    password_hash="not-used-in-this-test",
                ),
                product("mango"),
            ]
        )
        await session.commit()
        await create_order(session, order_id)
        purchases = PurchaseService(session)
        for receipt_date, quantity, cost in [
            (date(2026, 8, 1), Decimal("2"), Decimal("100")),
            (date(2026, 8, 2), Decimal("3"), Decimal("150")),
        ]:
            purchase = await purchases.create_purchase(
                supplier="Mandi supplier",
                purchase_date=receipt_date,
                items=[PurchaseItemDraft("mango", quantity, cost)],
                additional_costs=[],
                created_by_id=admin_id,
                cost_allocation_method=PurchaseCostAllocationMethod.BY_WEIGHT,
            )
            await purchases.receive_purchase(purchase.id)

        reservations = ReservationService(session)
        reserved = await reservations.reserve_stock(
            product_id="mango",
            quantity=Decimal("4"),
            order_id=order_id,
        )
        assert reserved.reserved_quantity == Decimal("4")
        assert reserved.shortage_quantity == Decimal("0")
        assert len(reserved.reservation_ids) == 2

        inventory = InventoryService(session)
        summary = await inventory.get_inventory_summary("mango")
        assert summary.physical == Decimal("5")
        assert summary.reserved == Decimal("4")
        assert summary.available == Decimal("1")
        assert await CostingService(session).calculate_cogs_for_reservation(
            reserved.reservation_ids[0]
        ) == Decimal("500")
        await session.rollback()

        assert await reservations.release_reservation(reserved.reservation_ids[1]) is True
        assert await reservations.release_reservation(reserved.reservation_ids[1]) is False
        assert await reservations.record_sale(reserved.reservation_ids[0]) == Decimal("200")
        assert await reservations.record_sale(reserved.reservation_ids[0]) is None

        summary = await inventory.get_inventory_summary("mango")
        assert summary.physical == Decimal("3")
        assert summary.reserved == Decimal("0")
        assert summary.available == Decimal("3")
        statuses = list(
            (
                await session.scalars(
                    select(InventoryReservation.status).order_by(InventoryReservation.created_at)
                )
            ).all()
        )
        assert sorted(statuses) == sorted(
            [
                InventoryReservationStatus.CONSUMED.value,
                InventoryReservationStatus.RELEASED.value,
            ]
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_fifo_reservations_never_over_allocate() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin_id = uuid.uuid4()
    order_ids = [uuid.uuid4(), uuid.uuid4()]

    async with factory() as session:
        session.add_all(
            [
                AdminUser(
                    id=admin_id,
                    email="concurrency-admin@example.com",
                    password_hash="not-used-in-this-test",
                ),
                product("mango"),
            ]
        )
        await session.commit()
        await create_order(session, order_ids[0])
        await create_order(session, order_ids[1])
        purchase = await PurchaseService(session).create_purchase(
            supplier="Mandi supplier",
            purchase_date=date(2026, 8, 1),
            items=[PurchaseItemDraft("mango", Decimal("10"), Decimal("100"))],
            additional_costs=[],
            created_by_id=admin_id,
            cost_allocation_method=PurchaseCostAllocationMethod.BY_WEIGHT,
        )
        await PurchaseService(session).receive_purchase(purchase.id)

    async def reserve(order_id: uuid.UUID):  # type: ignore[no-untyped-def]
        async with factory() as session:
            return await ReservationService(session).reserve_stock(
                product_id="mango",
                quantity=Decimal("7"),
                order_id=order_id,
            )

    first, second = await asyncio.gather(*(reserve(order_id) for order_id in order_ids))
    assert sorted([first.reserved_quantity, second.reserved_quantity]) == [
        Decimal("3"),
        Decimal("7"),
    ]
    assert first.shortage_quantity + second.shortage_quantity == Decimal("4")

    async with factory() as session:
        summary = await InventoryService(session).get_inventory_summary("mango")
        active = await session.scalar(
            select(func.coalesce(func.sum(InventoryReservation.quantity), Decimal("0"))).where(
                InventoryReservation.status == InventoryReservationStatus.ACTIVE.value
            )
        )
        negative_batches = await session.scalar(
            select(func.count())
            .select_from(InventoryBatch)
            .where(InventoryBatch.remaining_quantity < Decimal("0"))
        )
    await engine.dispose()
    assert summary.physical == Decimal("10")
    assert summary.available == Decimal("0")
    assert summary.reserved == Decimal("10")
    assert active == Decimal("10.000")
    assert negative_batches == 0


@pytest.mark.asyncio
async def test_order_transition_reserves_delivers_and_is_idempotent() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin_id = uuid.uuid4()
    rider_id = uuid.uuid4()
    order_id = uuid.uuid4()
    async with factory() as session:
        customer = Customer(phone="+923009999999", name="Ayesha", address="Lahore")
        order = Order(
            id=order_id,
            customer_phone=customer.phone,
            status=OrderStatus.PENDING.value,
            subtotal_pkr=500,
            delivery_charge_pkr=0,
            total_pkr=500,
            items=[
                OrderItem(
                    product_id="mango",
                    product_name="Mango",
                    unit_price_pkr=500,
                    quantity=Decimal("2"),
                    line_total_pkr=1000,
                )
            ],
        )
        session.add_all(
            [
                customer,
                order,
                AdminUser(id=admin_id, email="transition@example.com", password_hash="unused"),
                Rider(id=rider_id, name="Rider", phone="03001112222", is_active=True),
                product("mango"),
            ]
        )
        await session.commit()
        product_row = await session.get(Product, "mango")
        assert product_row is not None
        product_row.stock_quantity = Decimal("5")
        session.add(
            InventoryBatch(
                product_id="mango",
                received_quantity=Decimal("5"),
                remaining_quantity=Decimal("5"),
                unit_cost=Decimal("0"),
                effective_cost=Decimal("0"),
            )
        )
        await session.commit()

        transitions = OrderTransitionService(session)
        confirmed = await transitions.confirm_order(order_id, admin_id)
        assert confirmed.status == OrderStatus.CONFIRMED.value
        packed = await transitions.start_packing(order_id, admin_id)
        assert packed.status == OrderStatus.PACKING.value
        dispatched = await transitions.dispatch_order(order_id, rider_id, admin_id)
        assert dispatched.status == OrderStatus.DISPATCHED.value
        delivered = await transitions.deliver_order(order_id, admin_id)
        assert delivered.status == OrderStatus.DELIVERED.value
        with pytest.raises(DomainError, match="Only dispatched orders"):
            await transitions.deliver_order(order_id, admin_id)
    await engine.dispose()


@pytest.mark.asyncio
async def test_delivery_zone_schedule_and_rider_assignment() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    schedule = DeliveryScheduleService(cutoff_hour=15)
    assert schedule.calculate_delivery_date(datetime(2026, 8, 14, 9, tzinfo=UTC)) == date(
        2026, 8, 14
    )
    assert schedule.calculate_delivery_date(datetime(2026, 8, 14, 10, tzinfo=UTC)) == date(
        2026, 8, 15
    )

    async with factory() as session:
        zone = DeliveryZone(
            name="Johar Town",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [
                        [74.20, 31.40],
                        [74.30, 31.40],
                        [74.30, 31.50],
                        [74.20, 31.50],
                        [74.20, 31.40],
                    ]
                ],
            },
        )
        busy = Rider(id=uuid.uuid4(), name="Busy", phone="03001112222", is_active=True)
        quieter = Rider(id=uuid.uuid4(), name="Quieter", phone="03002223333", is_active=True)
        least_loaded = Rider(
            id=uuid.uuid4(), name="Least loaded", phone="03003334444", is_active=True
        )
        inactive = Rider(id=uuid.uuid4(), name="Inactive", phone="03004445555", is_active=False)
        admin = AdminUser(
            id=uuid.uuid4(),
            email="delivery-assignment@example.com",
            password_hash="unused",
        )
        customer = Customer(phone="+923008888888", name="Ayesha", address="Lahore")
        assignment_date = date(2026, 8, 16)
        order = Order(
            id=uuid.uuid4(),
            customer_phone=customer.phone,
            customer_name_snapshot=customer.name,
            delivery_address_snapshot=customer.address,
            status="pending",
            subtotal_pkr=0,
            delivery_charge_pkr=0,
            total_pkr=0,
            promised_delivery_date=assignment_date,
        )
        session.add_all([zone, busy, quieter, least_loaded, inactive, admin, customer, order])
        await session.flush()
        order.delivery_zone_id = zone.id
        daily_orders = [
            Order(
                customer_phone=customer.phone,
                customer_name_snapshot=customer.name,
                delivery_address_snapshot=customer.address,
                status="pending",
                subtotal_pkr=0,
                delivery_charge_pkr=0,
                total_pkr=0,
                promised_delivery_date=assignment_date,
                rider_id=rider_id,
            )
            for rider_id in [busy.id, busy.id, quieter.id]
        ]
        future_order = Order(
            customer_phone=customer.phone,
            customer_name_snapshot=customer.name,
            delivery_address_snapshot=customer.address,
            status="pending",
            subtotal_pkr=0,
            delivery_charge_pkr=0,
            total_pkr=0,
            promised_delivery_date=assignment_date + timedelta(days=1),
            rider_id=least_loaded.id,
        )
        session.add_all(
            [
                RiderZone(rider_id=busy.id, zone_id=zone.id),
                RiderZone(rider_id=quieter.id, zone_id=zone.id),
                RiderZone(rider_id=least_loaded.id, zone_id=zone.id),
                RiderZone(rider_id=inactive.id, zone_id=zone.id),
                *daily_orders,
                future_order,
            ]
        )
        await session.commit()

        zones = DeliveryZoneService(session)
        assert (await zones.resolve_zone(31.45, 74.25)).id == zone.id
        assert await zones.resolve_zone(31.80, 74.80) is None
        order_id = order.id
        busy_id = busy.id
        least_loaded_id = least_loaded.id
        admin_id = admin.id
        await session.rollback()
        assignment = RiderAssignmentService(session)
        assigned = await assignment.assign_rider(order_id)
        assert assigned is not None and assigned.id == least_loaded_id
        reassigned = await assignment.reassign_rider(order_id, busy_id, admin_id)
        assert reassigned.rider_id == busy_id
    await engine.dispose()


@pytest.mark.asyncio
async def test_replacing_rider_zones_deletes_old_memberships() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        original_zone = DeliveryZone(name="Original zone")
        retained_zone = DeliveryZone(name="Retained zone")
        replacement_zone = DeliveryZone(name="Replacement zone")
        rider = Rider(
            name="Zone replacement rider",
            phone="03005556666",
            is_active=True,
        )
        session.add_all([original_zone, retained_zone, replacement_zone, rider])
        await session.flush()
        session.add_all(
            [
                RiderZone(rider_id=rider.id, zone_id=original_zone.id),
                RiderZone(rider_id=rider.id, zone_id=retained_zone.id),
            ]
        )
        await session.commit()

        updated = await RiderService(session).set_rider_zones(
            rider.id,
            [retained_zone.id, replacement_zone.id, retained_zone.id],
        )

        expected_zone_ids = {retained_zone.id, replacement_zone.id}
        assert {membership.zone_id for membership in updated.rider_zones} == expected_zone_ids
        memberships = list(
            (
                await session.scalars(
                    select(RiderZone).where(RiderZone.rider_id == rider.id)
                )
            ).all()
        )
        assert {membership.zone_id for membership in memberships} == expected_zone_ids
        assert len(memberships) == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_inventory_http_workflow() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = AdminUser(
        id=uuid.uuid4(),
        email="inventory-http@example.com",
        password_hash="unused",
    )
    mango = product("mango")
    mango.inventory_mode = "tracked"

    async with factory() as session:
        session.add_all([admin, mango])
        await session.commit()
        admin_id = admin.id

        async def override_session():  # type: ignore[no-untyped-def]
            yield session

        async def override_admin() -> AdminUser:
            return AdminUser(
                id=admin_id,
                email="inventory-http@example.com",
                password_hash="unused",
            )

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_current_admin] = override_admin
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                invalid_cost = await client.post(
                    "/api/v1/admin/purchases",
                    json={
                        "supplier": "Mandi",
                        "purchase_date": "2026-08-14",
                        "items": [{"product_id": "mango", "quantity": "2", "unit_cost": "500"}],
                        "additional_costs": [{"cost_type": "invalid", "amount": "10"}],
                    },
                )
                assert invalid_cost.status_code == 422

                created = await client.post(
                    "/api/v1/admin/purchases",
                    json={
                        "supplier": "Mandi",
                        "purchase_date": "2026-08-14",
                        "items": [{"product_id": "mango", "quantity": "2", "unit_cost": "500"}],
                        "additional_costs": [{"cost_type": "transport", "amount": "100"}],
                        "cost_allocation_method": "by_weight",
                    },
                )
                assert created.status_code == 201
                purchase_id = created.json()["id"]

                updated = await client.put(
                    f"/api/v1/admin/purchases/{purchase_id}",
                    json={
                        "supplier": "Updated Mandi",
                        "purchase_date": "2026-08-15",
                        "items": [
                            {"product_id": "mango", "quantity": "3", "unit_cost": "450"}
                        ],
                        "additional_costs": [{"cost_type": "loading", "amount": "50"}],
                        "cost_allocation_method": "by_purchase_value",
                        "notes": "Updated draft",
                    },
                )
                assert updated.status_code == 200
                assert updated.json()["supplier"] == "Updated Mandi"
                assert updated.json()["total_cost"] == "1400.00"

                deletable = await client.post(
                    "/api/v1/admin/purchases",
                    json={
                        "supplier": "Temporary draft",
                        "purchase_date": "2026-08-15",
                        "items": [
                            {"product_id": "mango", "quantity": "1", "unit_cost": "100"}
                        ],
                    },
                )
                assert deletable.status_code == 201
                deletable_id = deletable.json()["id"]
                deleted = await client.delete(f"/api/v1/admin/purchases/{deletable_id}")
                assert deleted.status_code == 204
                missing = await client.get(f"/api/v1/admin/purchases/{deletable_id}")
                assert missing.status_code == 404

                received = await client.post(f"/api/v1/admin/purchases/{purchase_id}/receive")
                assert received.status_code == 200
                assert received.json()["status"] == "received"

                summary = await client.get("/api/v1/admin/products/mango/inventory")
                assert summary.status_code == 200
                assert summary.json()["physical"] == "2.000"
                batches = await client.get("/api/v1/admin/products/mango/batches")
                assert batches.status_code == 200
                assert len(batches.json()) == 1
                movements = await client.get("/api/v1/admin/products/mango/movements")
                assert movements.status_code == 200
                assert movements.json()["items"][0]["movement_type"] == "purchase"

                invalid_waste = await client.post(
                    "/api/v1/admin/inventory/waste",
                    json={"product_id": "mango", "quantity": "1", "reason": "invalid"},
                )
                assert invalid_waste.status_code == 422
                waste = await client.post(
                    "/api/v1/admin/inventory/waste",
                    json={"product_id": "mango", "quantity": "1", "reason": "rotten"},
                )
                assert waste.status_code == 201
                missing_reason = await client.post(
                    "/api/v1/admin/inventory/adjustments",
                    json={"product_id": "mango", "delta": "1"},
                )
                assert missing_reason.status_code == 422
        finally:
            app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_order_transition_http_workflow() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    admin = AdminUser(
        id=uuid.uuid4(),
        email="order-http@example.com",
        password_hash="unused",
    )
    customer = Customer(phone="+923001111111", name="Ayesha", address="Lahore")
    product_row = product("mango")
    product_row.inventory_mode = "tracked"
    product_row.stock_quantity = Decimal("5")
    rider = Rider(id=uuid.uuid4(), name="Rider", phone="03005556666", is_active=True)
    order = Order(
        id=uuid.uuid4(),
        customer_phone=customer.phone,
        status="pending",
        subtotal_pkr=500,
        delivery_charge_pkr=0,
        total_pkr=500,
        items=[
            OrderItem(
                product_id="mango",
                product_name="Mango",
                unit_price_pkr=500,
                quantity=Decimal("1"),
                line_total_pkr=500,
            )
        ],
    )
    async with factory() as session:
        session.add_all([admin, customer, product_row, rider, order])
        await session.commit()
        admin_id = admin.id
        order_id = order.id
        rider_id = rider.id

        async def override_session():  # type: ignore[no-untyped-def]
            yield session

        async def override_admin() -> AdminUser:
            return AdminUser(id=admin_id, email="order-http@example.com", password_hash="unused")

        app.dependency_overrides[get_session] = override_session
        app.dependency_overrides[get_current_admin] = override_admin
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                missing_reason = await client.post(
                    f"/api/v1/admin/orders/{order_id}/cancel", json={}
                )
                assert missing_reason.status_code == 400
                confirmed = await client.post(f"/api/v1/admin/orders/{order_id}/confirm")
                assert confirmed.status_code == 200
                assert confirmed.json()["status"] == "confirmed"
                invalid = await client.post(f"/api/v1/admin/orders/{order_id}/deliver")
                assert invalid.status_code == 409
                packed = await client.post(f"/api/v1/admin/orders/{order_id}/start-packing")
                assert packed.status_code == 200
                dispatched = await client.post(
                    f"/api/v1/admin/orders/{order_id}/dispatch",
                    json={"rider_id": str(rider_id)},
                )
                assert dispatched.status_code == 200
                assert dispatched.json()["rider_id"] == str(rider_id)
                delivered = await client.post(f"/api/v1/admin/orders/{order_id}/deliver")
                assert delivered.status_code == 200
                history = await client.get(f"/api/v1/admin/orders/{order_id}/status-history")
                assert history.status_code == 200
                assert len(history.json()) >= 4
        finally:
            app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_rider_mobile_api_scopes_orders_and_retries_delivery() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    today = datetime.now(KARACHI).date()
    first_rider = Rider(
        id=uuid.uuid4(),
        name="First Rider",
        phone="03001112222",
        password_hash=hash_password("first-rider-password"),
        is_active=True,
    )
    second_rider = Rider(
        id=uuid.uuid4(),
        name="Second Rider",
        phone="03003334444",
        password_hash=hash_password("second-rider-password"),
        is_active=True,
    )
    customer = Customer(phone="+923005555555", name="Ayesha", address="Model Town")
    assigned = Order(
        id=uuid.uuid4(),
        customer_phone=customer.phone,
        status="dispatched",
        subtotal_pkr=500,
        delivery_charge_pkr=0,
        total_pkr=500,
        rider_id=first_rider.id,
        promised_delivery_date=today,
        items=[
            OrderItem(
                product_id="mango",
                product_name="Mango",
                unit_price_pkr=500,
                quantity=Decimal("1"),
                line_total_pkr=500,
            )
        ],
    )
    other_rider_order = Order(
        id=uuid.uuid4(),
        customer_phone=customer.phone,
        status="dispatched",
        subtotal_pkr=500,
        delivery_charge_pkr=0,
        total_pkr=500,
        rider_id=second_rider.id,
        promised_delivery_date=today,
    )
    tomorrow_order = Order(
        id=uuid.uuid4(),
        customer_phone=customer.phone,
        status="dispatched",
        subtotal_pkr=500,
        delivery_charge_pkr=0,
        total_pkr=500,
        rider_id=first_rider.id,
        promised_delivery_date=today + timedelta(days=1),
    )
    async with factory() as session:
        session.add_all(
            [first_rider, second_rider, customer, assigned, other_rider_order, tomorrow_order]
        )
        await session.commit()
        first_rider_phone = first_rider.phone
        assigned_id = assigned.id
        other_rider_order_id = other_rider_order.id

        async def override_session():  # type: ignore[no-untyped-def]
            yield session

        app.dependency_overrides[get_session] = override_session
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                login = await client.post(
                    "/api/v1/rider/auth/login",
                    json={"phone": first_rider_phone, "password": "first-rider-password"},
                )
                assert login.status_code == 200
                token = login.json()["access_token"]
                headers = {"Authorization": f"Bearer {token}"}
                today_response = await client.get(
                    "/api/v1/rider/me/deliveries/today", headers=headers
                )
                assert today_response.status_code == 200
                assert [item["id"] for item in today_response.json()] == [str(assigned_id)]
                assert "total_pkr" not in today_response.json()[0]
                forbidden = await client.post(
                    f"/api/v1/rider/orders/{other_rider_order_id}/delivered", headers=headers
                )
                assert forbidden.status_code == 403
                started = await client.post(
                    f"/api/v1/rider/orders/{assigned_id}/start", headers=headers
                )
                assert started.status_code == 200
                assert started.json()["rider_started_at"] is not None
                delivered = await client.post(
                    f"/api/v1/rider/orders/{assigned_id}/delivered", headers=headers
                )
                assert delivered.status_code == 200
                retry = await client.post(
                    f"/api/v1/rider/orders/{assigned_id}/delivered", headers=headers
                )
                assert retry.status_code == 200
                assert retry.json()["status"] == "delivered"
        finally:
            app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_customer_aggregates_use_realized_orders_and_recent_favourites() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    customer = Customer(phone="+923007777777", name="Ayesha", address="Model Town")
    delivered = Order(
        id=uuid.uuid4(),
        customer_phone=customer.phone,
        status="delivered",
        subtotal_pkr=100,
        delivery_charge_pkr=0,
        total_pkr=100,
        created_at=now - timedelta(days=10),
        items=[
            OrderItem(
                product_id="mango",
                product_name="Mango",
                unit_price_pkr=50,
                quantity=Decimal("2"),
                line_total_pkr=100,
            )
        ],
    )
    completed = Order(
        id=uuid.uuid4(),
        customer_phone=customer.phone,
        status="completed",
        subtotal_pkr=200,
        delivery_charge_pkr=0,
        total_pkr=200,
        created_at=now - timedelta(days=20),
        items=[
            OrderItem(
                product_id="mango",
                product_name="Mango",
                unit_price_pkr=50,
                quantity=Decimal("1"),
                line_total_pkr=50,
            ),
            OrderItem(
                product_id="banana",
                product_name="Banana",
                unit_price_pkr=150,
                quantity=Decimal("1"),
                line_total_pkr=150,
            ),
        ],
    )
    pending = Order(
        id=uuid.uuid4(), customer_phone=customer.phone, status="pending", subtotal_pkr=999,
        delivery_charge_pkr=0, total_pkr=999, created_at=now - timedelta(days=5),
    )
    old = Order(
        id=uuid.uuid4(), customer_phone=customer.phone, status="delivered", subtotal_pkr=500,
        delivery_charge_pkr=0, total_pkr=500, created_at=now - timedelta(days=100),
    )
    async with factory() as session:
        session.add_all([customer, delivered, completed, pending, old])
        await session.commit()
        listing = await list_customers(session, page=1, page_size=25, query="Ayesha", sort="spend")
        assert listing.total == 1
        assert listing.items[0].lifetime_spend_pkr == 800
        assert listing.items[0].order_frequency_90d == 2
        recent = await list_customers(
            session,
            page=1,
            page_size=25,
            query="Ayesha",
            sort="spend",
            segment=CustomerSegment.RECENT,
        )
        assert recent.total == 1
        inactive = await list_customers(
            session,
            page=1,
            page_size=25,
            query="Ayesha",
            sort="spend",
            segment=CustomerSegment.INACTIVE,
        )
        assert inactive.total == 0
        detail = await customer_detail(session, customer.phone)
        assert detail.lifetime_spend_pkr == 800
        assert detail.order_frequency_90d == 2
        assert [(item.product_id, item.quantity) for item in detail.favorite_items] == [
            ("mango", Decimal("3.000")),
            ("banana", Decimal("1.000")),
        ]
    await engine.dispose()


@pytest.mark.asyncio
async def test_public_checkout_projects_default_on_demand_shortage_into_procurement() -> None:
    assert TEST_DATABASE_URL is not None
    await reset_database()
    await asyncio.to_thread(run_migrations)
    engine = create_async_engine(async_url(TEST_DATABASE_URL))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        catalog_product = product("mango")
        # Admin-created tracked products use arrange-on-demand unless operations
        # explicitly selects the restrictive in-stock-only policy.
        catalog_product.inventory_mode = "tracked"
        catalog_product.stock_quantity = Decimal("3")
        catalog_product.low_stock_threshold = Decimal("5")
        catalog_product.stock_policy = None
        catalog_product.base_price_pkr = 500
        zone = DeliveryZone(
            name="Checkout Zone",
            boundary={
                "type": "Polygon",
                "coordinates": [
                    [
                        [74.20, 31.40],
                        [74.40, 31.40],
                        [74.40, 31.60],
                        [74.20, 31.40],
                    ]
                ],
            },
        )
        session.add_all([catalog_product, zone])
        await session.commit()

        async def override_session():  # type: ignore[no-untyped-def]
            yield session

        app.dependency_overrides[get_session] = override_session
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/v1/orders",
                    headers={"Idempotency-Key": str(uuid.uuid4())},
                    json={
                        "customer": {
                            "name": "Ayesha",
                            "phone": "03001234567",
                            "address": "Johar Town",
                        },
                        "delivery_location": {"latitude": "31.470000", "longitude": "74.280000"},
                        "items": [
                            {
                                "product_id": "mango",
                                "quantity": "5",
                                "unit_price_pkr": 1,
                                "line_total_pkr": 1,
                            }
                        ],
                        "total_pkr": 1,
                    },
                )
                assert response.status_code == 201
                body = response.json()
                assert body["subtotal_pkr"] == 2500
                assert body["total_pkr"] == 2500
                assert body["total_pkr"] != 1
                assert body["customer"]["name"] == "Ayesha"
                assert body["customer"]["address"] == "Johar Town"
                saved_order = await session.get(Order, uuid.UUID(body["id"]))
                assert saved_order is not None
                assert saved_order.delivery_zone_id == zone.id
                assert not {"cogs_pkr", "internal_fulfillment_status"}.intersection(body)

                requirements = await InventoryReadService(session).procurement_requirements()
                assert len(requirements) == 1
                requirement = requirements[0]
                assert requirement.product_id == "mango"
                assert requirement.current_stock_quantity == Decimal("3")
                assert requirement.pending_order_quantity == Decimal("5")
                assert requirement.shortage_quantity == Decimal("2")
                assert requirement.low_stock_replenishment_quantity == Decimal("5")
                assert requirement.suggested_purchase_quantity == Decimal("7")
                assert requirement.pending_order_count == 1
                assert requirement.order_ids == [saved_order.id]
        finally:
            app.dependency_overrides.clear()
    await engine.dispose()
