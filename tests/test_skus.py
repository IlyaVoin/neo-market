import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Category, OutboxEvent, Product, ProductStatus, SKU
from src.schemas.sku import SKUCreate


class TestCreateSKU:
    @pytest.mark.asyncio
    async def test_first_sku_transitions_product_to_on_moderation(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product first
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.CREATED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Add first SKU
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [
                    {"name": "Цвет", "value": "Чёрный"},
                    {"name": "Объём памяти", "value": "256 ГБ"},
                ],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["product_id"] == str(product.id)
        assert data["name"] == "256GB Black"
        assert data["price"] == 12999000
        assert data["cost_price"] == 9500000
        assert data["active_quantity"] == 0
        assert data["reserved_quantity"] == 0
        assert len(data["characteristics"]) == 2

        # Verify product status changed to ON_MODERATION
        await session.refresh(product)
        assert product.status == ProductStatus.ON_MODERATION

    @pytest.mark.asyncio
    async def test_first_sku_emits_created_event_to_moderation(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product first
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.CREATED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Add first SKU
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 201

        # Verify outbox event was created
        stmt = select(OutboxEvent).where(OutboxEvent.aggregate_id == product.id)
        result = await session.execute(stmt)
        events = result.scalars().all()

        assert len(events) == 1
        event = events[0]
        assert event.event_type == "product.created"
        assert event.aggregate_id == product.id
        assert event.payload["event"] == "CREATED"
        assert event.payload["product_id"] == str(product.id)
        assert event.payload["seller_id"] == str(seller_id_from_token)
        assert "idempotency_key" in event.payload
        assert "date" in event.payload
        assert event.service_key == "dev-b2b-to-mod-key"
        assert event.target_url == "http://moderation:8000/api/v1/events/product"
        assert event.processed_at is None
        assert event.retry_count == 0

    @pytest.mark.asyncio
    async def test_second_sku_transitions_moderated_product_to_on_moderation_and_emits_edited(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product with MODERATED status (already has one SKU)
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.MODERATED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Add first SKU (simulating existing SKU)
        existing_sku = SKU(
            product_id=product.id,
            name="128GB Black",
            price=11999000,
            cost_price=8500000,
            discount=0,
            image="/s3/iphone15-black-128.jpg",
            active_quantity=0,
            reserved_quantity=0,
        )
        session.add(existing_sku)
        await session.commit()

        # Add second SKU
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 201

        # Verify product status changed to ON_MODERATION
        await session.refresh(product)
        assert product.status == ProductStatus.ON_MODERATION

        # Verify EDITED event was emitted
        stmt = select(OutboxEvent).where(OutboxEvent.aggregate_id == product.id)
        result = await session.execute(stmt)
        events = result.scalars().all()

        # Should have 1 event (EDITED) - the first SKU was added directly, not via API
        assert len(events) == 1
        event = events[0]
        assert event.event_type == "product.edited"
        assert event.payload["event"] == "EDITED"

    @pytest.mark.asyncio
    async def test_add_sku_to_hard_blocked_returns_403(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product with HARD_BLOCKED status
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.HARD_BLOCKED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Try to add SKU
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 403
        data = response.json()
        assert data["code"] == "FORBIDDEN"
        assert data["message"] == "Cannot add SKU to hard-blocked product"

    @pytest.mark.asyncio
    async def test_missing_image_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.CREATED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Try to add SKU without image
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "characteristics": [],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_missing_price_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.CREATED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Try to add SKU without price
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_invalid_product_id_returns_404(
        self,
        client: AsyncClient,
        valid_token: str,
    ):
        # Try to add SKU to non-existent product
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(uuid.uuid4()),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"
        assert data["message"] == "Product not found"

    @pytest.mark.asyncio
    async def test_sku_ownership_check_returns_404(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create another seller
        other_seller_id = uuid.uuid4()
        other_token = "Bearer " + __import__("jose.jwt").jwt.encode(
            {"sub": str(other_seller_id)}, "dev-secret-change-in-production", algorithm="HS256"
        )

        # Create product owned by first seller
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.CREATED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Try to add SKU with different seller's token
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [],
            },
            headers={"Authorization": other_token},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"
        assert data["message"] == "Product not found"

    @pytest.mark.asyncio
    async def test_sku_with_characteristics(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.CREATED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Add SKU with characteristics
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 500000,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [
                    {"name": "Цвет", "value": "Чёрный"},
                    {"name": "Объём памяти", "value": "256 ГБ"},
                ],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert len(data["characteristics"]) == 2
        assert data["characteristics"][0]["name"] == "Цвет"
        assert data["characteristics"][0]["value"] == "Чёрный"
        assert data["characteristics"][1]["name"] == "Объём памяти"
        assert data["characteristics"][1]["value"] == "256 ГБ"
        assert data["discount"] == 500000

    @pytest.mark.asyncio
    async def test_second_sku_to_blocked_product_transitions_to_on_moderation(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        session: AsyncSession,
        seller_id_from_token: uuid.UUID,
    ):
        # Create a product with BLOCKED status and existing SKU
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Test Product",
            description="Test Description",
            slug="test-product",
            status=ProductStatus.BLOCKED,
        )
        session.add(product)
        await session.commit()
        await session.refresh(product)

        # Add existing SKU
        existing_sku = SKU(
            product_id=product.id,
            name="128GB Black",
            price=11999000,
            cost_price=8500000,
            discount=0,
            image="/s3/iphone15-black-128.jpg",
            active_quantity=0,
            reserved_quantity=0,
        )
        session.add(existing_sku)
        await session.commit()

        # Add second SKU
        response = await client.post(
            "/api/v1/skus",
            json={
                "product_id": str(product.id),
                "name": "256GB Black",
                "price": 12999000,
                "cost_price": 9500000,
                "discount": 0,
                "image": "/s3/iphone15-black-256.jpg",
                "characteristics": [],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 201

        # Verify product status changed to ON_MODERATION
        await session.refresh(product)
        assert product.status == ProductStatus.ON_MODERATION

        # Verify EDITED event was emitted
        stmt = select(OutboxEvent).where(OutboxEvent.aggregate_id == product.id)
        result = await session.execute(stmt)
        events = result.scalars().all()

        assert len(events) == 1
        event = events[0]
        assert event.event_type == "product.edited"
        assert event.payload["event"] == "EDITED"