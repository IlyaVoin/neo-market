import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import SKU, Category, OutboxEvent, Product, ProductStatus
from src.models.inventory import ReserveOperation

SERVICE_HEADERS = {"X-Service-Key": settings.b2b_to_b2c_key}


async def _seed_product(session: AsyncSession, category: Category) -> Product:
    product = Product(
        seller_id=uuid.uuid4(),
        category_id=category.id,
        title="iPhone 15 Pro Max",
        description="Flagship",
        slug="iphone-15-pro-max",
        status=ProductStatus.MODERATED,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def _seed_sku(
    session: AsyncSession,
    product: Product,
    active_quantity: int,
    reserved_quantity: int = 0,
) -> SKU:
    sku = SKU(
        product_id=product.id,
        name="256GB Black",
        price=12999000,
        cost_price=9500000,
        discount=0,
        image="/s3/iphone15-black-256.jpg",
        active_quantity=active_quantity,
        reserved_quantity=reserved_quantity,
    )
    session.add(sku)
    await session.commit()
    await session.refresh(sku)
    return sku


class TestReserve:
    @pytest.mark.asyncio
    async def test_reserve_all_skus_succeeds(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku1 = await _seed_sku(session, product, active_quantity=10)
        sku2 = await _seed_sku(session, product, active_quantity=5)

        response = await client.post(
            "/api/v1/reserve",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "items": [
                    {"sku_id": str(sku1.id), "quantity": 2},
                    {"sku_id": str(sku2.id), "quantity": 1},
                ],
            },
            headers=SERVICE_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["reserved"] is True
        by_sku = {item["sku_id"]: item for item in data["items"]}
        assert by_sku[str(sku1.id)]["reserved_quantity"] == 2
        assert by_sku[str(sku1.id)]["remaining_stock"] == 8
        assert by_sku[str(sku2.id)]["reserved_quantity"] == 1
        assert by_sku[str(sku2.id)]["remaining_stock"] == 4

        await session.refresh(sku1)
        await session.refresh(sku2)
        assert sku1.active_quantity == 8
        assert sku1.reserved_quantity == 2
        assert sku2.active_quantity == 4
        assert sku2.reserved_quantity == 1
        # Инвариант: active + reserved == on_hand (не изменился)
        assert sku1.active_quantity + sku1.reserved_quantity == 10
        assert sku2.active_quantity + sku2.reserved_quantity == 5

    @pytest.mark.asyncio
    async def test_partial_insufficient_stock_returns_409_all_rollback(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku1 = await _seed_sku(session, product, active_quantity=5)
        sku2 = await _seed_sku(session, product, active_quantity=1)
        sku1_id, sku2_id = str(sku1.id), str(sku2.id)

        response = await client.post(
            "/api/v1/reserve",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "items": [
                    {"sku_id": sku1_id, "quantity": 2},
                    {"sku_id": sku2_id, "quantity": 3},
                ],
            },
            headers=SERVICE_HEADERS,
        )

        assert response.status_code == 409
        data = response.json()
        assert data["reserved"] is False
        failed = {item["sku_id"]: item for item in data["failed_items"]}
        assert sku2_id in failed
        assert failed[sku2_id]["reason"] == "INSUFFICIENT_STOCK"
        assert failed[sku2_id]["requested"] == 3
        assert failed[sku2_id]["available"] == 1

        # Ни один SKU не зарезервирован.
        await session.refresh(sku1)
        await session.refresh(sku2)
        assert sku1.active_quantity == 5
        assert sku1.reserved_quantity == 0
        assert sku2.active_quantity == 1
        assert sku2.reserved_quantity == 0

    @pytest.mark.asyncio
    async def test_idempotent_reserve_returns_200_without_double_deduction(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        key = str(uuid.uuid4())
        body = {
            "idempotency_key": key,
            "items": [{"sku_id": str(sku.id), "quantity": 3}],
        }

        first = await client.post("/api/v1/reserve", json=body, headers=SERVICE_HEADERS)
        assert first.status_code == 200

        await session.refresh(sku)
        assert sku.active_quantity == 7
        assert sku.reserved_quantity == 3

        second = await client.post("/api/v1/reserve", json=body, headers=SERVICE_HEADERS)
        assert second.status_code == 200
        assert second.json()["reserved"] is True

        # Повторный запрос не списывает повторно.
        await session.refresh(sku)
        assert sku.active_quantity == 7
        assert sku.reserved_quantity == 3

        stmt = select(ReserveOperation).where(
            ReserveOperation.idempotency_key == uuid.UUID(key),
            ReserveOperation.operation_type == "reserve",
        )
        result = await session.execute(stmt)
        assert len(result.scalars().all()) == 1

    @pytest.mark.asyncio
    async def test_sku_out_of_stock_event_emitted(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=2)

        response = await client.post(
            "/api/v1/reserve",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "items": [{"sku_id": str(sku.id), "quantity": 2}],
            },
            headers=SERVICE_HEADERS,
        )
        assert response.status_code == 200

        await session.refresh(sku)
        assert sku.active_quantity == 0
        assert sku.reserved_quantity == 2

        stmt = select(OutboxEvent).where(OutboxEvent.event_type == "sku.out_of_stock")
        result = await session.execute(stmt)
        events = result.scalars().all()
        assert len(events) == 1
        event = events[0]
        assert event.payload["event"] == "SKU_OUT_OF_STOCK"
        assert event.payload["sku_id"] == str(sku.id)
        assert event.payload["product_id"] == str(product.id)
        assert "idempotency_key" in event.payload
        assert event.aggregate_id == sku.id
        assert event.service_key == settings.b2b_to_b2c_key
        assert event.target_url.endswith("/api/v1/events/inventory")
        assert event.processed_at is None

    @pytest.mark.asyncio
    async def test_unreserve_restores_quantities(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=8, reserved_quantity=2)
        on_hand = sku.active_quantity + sku.reserved_quantity

        response = await client.post(
            "/api/v1/unreserve",
            json={
                "order_id": str(uuid.uuid4()),
                "items": [{"sku_id": str(sku.id), "quantity": 2}],
            },
            headers=SERVICE_HEADERS,
        )

        assert response.status_code == 200
        assert response.json() == {"ok": True}

        await session.refresh(sku)
        assert sku.active_quantity == 10
        assert sku.reserved_quantity == 0
        assert sku.active_quantity + sku.reserved_quantity == on_hand


class TestInventoryEdgeCases:
    @pytest.mark.asyncio
    async def test_reserve_missing_service_key_returns_401(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=5)

        response = await client.post(
            "/api/v1/reserve",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "items": [{"sku_id": str(sku.id), "quantity": 1}],
            },
        )

        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_unreserve_idempotent_by_order_id(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=8, reserved_quantity=2)
        body = {
            "order_id": str(uuid.uuid4()),
            "items": [{"sku_id": str(sku.id), "quantity": 2}],
        }

        first = await client.post("/api/v1/unreserve", json=body, headers=SERVICE_HEADERS)
        assert first.status_code == 200
        await session.refresh(sku)
        assert sku.active_quantity == 10
        assert sku.reserved_quantity == 0

        # Повтор unreserve с тем же order_id не меняет количества повторно.
        second = await client.post("/api/v1/unreserve", json=body, headers=SERVICE_HEADERS)
        assert second.status_code == 200
        await session.refresh(sku)
        assert sku.active_quantity == 10
        assert sku.reserved_quantity == 0

    @pytest.mark.asyncio
    async def test_openapi_reserve_endpoint_returns_openapi_shape(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=5)
        order_id = uuid.uuid4()

        response = await client.post(
            "/api/v1/inventory/reserve",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "order_id": str(order_id),
                "items": [{"sku_id": str(sku.id), "quantity": 2}],
            },
            headers=SERVICE_HEADERS,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["order_id"] == str(order_id)
        assert data["status"] == "RESERVED"
        assert "reserved_at" in data

        await session.refresh(sku)
        assert sku.active_quantity == 3
        assert sku.reserved_quantity == 2
