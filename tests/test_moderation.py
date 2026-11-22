import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import SKU, Category, OutboxEvent, Product, ProductStatus
from src.models.moderation import ProcessedEvent

MOD_HEADERS = {"X-Service-Key": settings.mod_to_b2b_key}


async def _seed_product(
    session: AsyncSession,
    category: Category,
    seller_id: uuid.UUID,
    status: ProductStatus = ProductStatus.MODERATED,
    **kwargs,
) -> Product:
    product = Product(
        seller_id=seller_id,
        category_id=category.id,
        title="iPhone 15 Pro Max",
        description="Flagship",
        slug="iphone-15-pro-max",
        status=status,
        **kwargs,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def _seed_sku(session: AsyncSession, product: Product) -> SKU:
    sku = SKU(
        product_id=product.id,
        name="256GB Black",
        price=12999000,
        cost_price=9500000,
        discount=0,
        image="/s3/iphone15-black-256.jpg",
        active_quantity=10,
        reserved_quantity=0,
    )
    session.add(sku)
    await session.commit()
    await session.refresh(sku)
    return sku


class TestModerationEvents:
    @pytest.mark.asyncio
    async def test_moderated_event_clears_blocking_data(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(
            session,
            test_category,
            seller_id_from_token,
            status=ProductStatus.BLOCKED,
            blocking_reason_id=uuid.uuid4(),
            blocking_reason_title="Описание не соответствует товару",
            moderator_comment="Несоответствие описания и фотографий",
            field_reports=[{"field_name": "description", "sku_id": None, "comment": "Спам"}],
        )
        product_id = product.id

        response = await client.post(
            "/api/v1/events/moderation",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "product_id": str(product_id),
                "status": "MODERATED",
            },
            headers=MOD_HEADERS,
        )

        assert response.status_code == 200

        await session.refresh(product)
        assert product.status == ProductStatus.MODERATED
        assert product.blocking_reason_id is None
        assert product.blocking_reason_title is None
        assert product.moderator_comment is None
        assert product.field_reports == []

        detail = await client.get(
            f"/api/v1/products/{product_id}",
            headers={"X-Service-Key": settings.b2b_to_b2c_key},
        )
        data = detail.json()
        assert data["blocking_reason"] is None
        assert data["field_reports"] == []

    @pytest.mark.asyncio
    async def test_blocked_soft_saves_field_reports(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        sku = await _seed_sku(session, product)
        product_id, sku_id = product.id, sku.id
        reason_id = uuid.uuid4()

        response = await client.post(
            "/api/v1/events/moderation",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "product_id": str(product_id),
                "status": "BLOCKED",
                "hard_block": False,
                "blocking_reason": {
                    "id": str(reason_id),
                    "title": "Описание не соответствует товару",
                    "comment": "Несоответствие описания и фотографий",
                },
                "field_reports": [
                    {"field_name": "description", "sku_id": None, "comment": "Копипаст"}
                ],
            },
            headers=MOD_HEADERS,
        )

        assert response.status_code == 200

        await session.refresh(product)
        assert product.status == ProductStatus.BLOCKED
        assert product.blocking_reason_id == reason_id
        assert product.blocking_reason_title == "Описание не соответствует товару"
        assert product.moderator_comment == "Несоответствие описания и фотографий"
        assert len(product.field_reports) == 1
        assert product.field_reports[0]["field_name"] == "description"

        # Каскад PRODUCT_BLOCKED в B2C.
        stmt = select(OutboxEvent).where(OutboxEvent.event_type == "product.product_blocked")
        result = await session.execute(stmt)
        events = result.scalars().all()
        assert len(events) == 1
        event = events[0]
        assert event.payload["event"] == "PRODUCT_BLOCKED"
        assert event.payload["product_id"] == str(product_id)
        assert event.payload["sku_ids"] == [str(sku_id)]
        assert event.service_key == settings.b2b_to_b2c_key
        assert event.target_url.endswith("/api/v1/events/product")

        # Продавец видит field_reports.
        detail = await client.get(
            f"/api/v1/products/{product_id}",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert detail.status_code == 200
        assert len(detail.json()["field_reports"]) == 1

    @pytest.mark.asyncio
    async def test_blocked_hard_sets_terminal_status(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        product_id = product.id

        response = await client.post(
            "/api/v1/events/moderation",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "product_id": str(product_id),
                "status": "BLOCKED",
                "hard_block": True,
                "blocking_reason": {
                    "id": str(uuid.uuid4()),
                    "title": "Контрафакт",
                    "comment": "Подделка",
                },
                "field_reports": [],
            },
            headers=MOD_HEADERS,
        )

        assert response.status_code == 200

        await session.refresh(product)
        assert product.status == ProductStatus.HARD_BLOCKED

        stmt = select(OutboxEvent).where(OutboxEvent.event_type == "product.product_blocked")
        result = await session.execute(stmt)
        assert len(result.scalars().all()) == 1

    @pytest.mark.asyncio
    async def test_hard_blocked_product_rejects_seller_edits(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(
            session, test_category, seller_id_from_token, status=ProductStatus.HARD_BLOCKED
        )
        product_id = product.id

        patch = await client.patch(
            f"/api/v1/products/{product_id}",
            json={"title": "Взлом"},
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert patch.status_code == 403
        assert patch.json()["code"] == "FORBIDDEN"

        delete = await client.delete(
            f"/api/v1/products/{product_id}",
            headers={"Authorization": f"Bearer {valid_token}"},
        )
        assert delete.status_code == 403
        assert delete.json()["code"] == "FORBIDDEN"

        await session.refresh(product)
        assert product.deleted is False

    @pytest.mark.asyncio
    async def test_duplicate_event_same_idempotency_key_no_side_effects(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        product_id = product.id
        key = str(uuid.uuid4())
        body = {
            "idempotency_key": key,
            "product_id": str(product_id),
            "status": "BLOCKED",
            "hard_block": False,
            "blocking_reason": {"id": str(uuid.uuid4()), "title": "Причина", "comment": "Коммент"},
            "field_reports": [],
        }

        first = await client.post("/api/v1/events/moderation", json=body, headers=MOD_HEADERS)
        assert first.status_code == 200
        assert first.json()["duplicate"] is False

        stmt = select(OutboxEvent).where(OutboxEvent.event_type == "product.product_blocked")
        events_after_first = (await session.execute(stmt)).scalars().all()
        assert len(events_after_first) == 1

        second = await client.post("/api/v1/events/moderation", json=body, headers=MOD_HEADERS)
        assert second.status_code == 200
        assert second.json()["duplicate"] is True

        # Никаких новых побочных эффектов.
        events_after_second = (await session.execute(stmt)).scalars().all()
        assert len(events_after_second) == 1

        await session.refresh(product)
        assert product.status == ProductStatus.BLOCKED

        stmt_pe = select(ProcessedEvent).where(
            ProcessedEvent.idempotency_key == uuid.UUID(key)
        )
        assert len((await session.execute(stmt_pe)).scalars().all()) == 1


class TestModerationEdgeCases:
    @pytest.mark.asyncio
    async def test_missing_service_key_returns_401(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)

        response = await client.post(
            "/api/v1/events/moderation",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "product_id": str(product.id),
                "status": "MODERATED",
            },
        )

        assert response.status_code == 401
        assert response.json()["code"] == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_unknown_product_returns_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
    ):
        response = await client.post(
            "/api/v1/events/moderation",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "product_id": str(uuid.uuid4()),
                "status": "MODERATED",
            },
            headers=MOD_HEADERS,
        )

        assert response.status_code == 404
        assert response.json()["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_openapi_endpoint_returns_204(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        product_id = product.id

        response = await client.post(
            "/api/v1/moderation/events",
            json={
                "idempotency_key": str(uuid.uuid4()),
                "product_id": str(product_id),
                "event_type": "MODERATED",
                "occurred_at": "2026-03-15T14:30:00.000Z",
            },
            headers=MOD_HEADERS,
        )

        assert response.status_code == 204

        await session.refresh(product)
        assert product.status == ProductStatus.MODERATED
