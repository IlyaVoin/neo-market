import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import (
    SKU,
    Category,
    Product,
    ProductCharacteristic,
    ProductImage,
    ProductStatus,
    SKUCharacteristic,
)

CATALOG_HEADERS = {"X-Service-Key": settings.b2b_to_b2c_key}


async def _seed_card_product(
    session: AsyncSession,
    category: Category,
    *,
    status: ProductStatus = ProductStatus.MODERATED,
    deleted: bool = False,
    with_skus: bool = True,
) -> Product:
    product = Product(
        seller_id=uuid.uuid4(),
        category_id=category.id,
        title="Футболка",
        description="Хлопковая футболка",
        slug=f" футболка-{uuid.uuid4().hex[:6]}".strip(),
        status=status,
        deleted=deleted,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)

    session.add(
        ProductImage(product_id=product.id, url="/s3/1.jpg", ordering=0)
    )
    session.add(
        ProductImage(product_id=product.id, url="/s3/2.jpg", ordering=1)
    )
    session.add(
        ProductCharacteristic(product_id=product.id, name="Бренд", value="Acme")
    )

    if with_skus:
        sku_a = SKU(
            product_id=product.id,
            name="Красная M",
            price=1000,
            cost_price=400,
            discount=200,
            image="/s3/red-m.jpg",
            active_quantity=5,
            reserved_quantity=2,
        )
        sku_b = SKU(
            product_id=product.id,
            name="Синяя L",
            price=1500,
            cost_price=700,
            discount=0,
            image="/s3/blue-l.jpg",
            active_quantity=0,
            reserved_quantity=0,
        )
        session.add(sku_a)
        session.add(sku_b)
        await session.commit()
        await session.refresh(sku_a)
        session.add(
            SKUCharacteristic(sku_id=sku_a.id, name="Цвет", value="Красный")
        )
        session.add(
            SKUCharacteristic(sku_id=sku_a.id, name="Размер", value="M")
        )

    await session.commit()
    await session.refresh(product)
    return product


class TestProductCardHappyPath:
    @pytest.mark.asyncio
    async def test_product_card_returns_full_data_with_skus(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_card_product(session, test_category)

        response = await client.get(
            f"/api/v1/catalog/products/{product.id}", headers=CATALOG_HEADERS
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(product.id)
        assert data["title"] == "Футболка"
        assert data["description"] == "Хлопковая футболка"
        assert data["status"] == ProductStatus.MODERATED.value
        assert {img["url"] for img in data["images"]} == {"/s3/1.jpg", "/s3/2.jpg"}
        assert data["characteristics"][0]["name"] == "Бренд"

        skus = data["skus"]
        assert len(skus) == 2
        by_name = {sku["name"]: sku for sku in skus}
        red = by_name["Красная M"]
        assert red["price"] == 1000
        assert red["discount"] == 200
        assert red["active_quantity"] == 5
        assert red["in_stock"] is True
        assert {c["name"] for c in red["characteristics"]} == {"Цвет", "Размер"}

    @pytest.mark.asyncio
    async def test_cost_price_absent_in_response(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_card_product(session, test_category)

        response = await client.get(
            f"/api/v1/catalog/products/{product.id}", headers=CATALOG_HEADERS
        )

        assert response.status_code == 200
        skus = response.json()["skus"]
        assert len(skus) >= 1
        for sku in skus:
            assert "cost_price" not in sku
            assert "reserved_quantity" not in sku
        assert "seller_id" not in response.json()
        assert "blocking_reason" not in response.json()
        assert "moderator_comment" not in response.json()

    @pytest.mark.asyncio
    async def test_sku_without_stock_is_shown_as_unavailable(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_card_product(session, test_category)

        response = await client.get(
            f"/api/v1/catalog/products/{product.id}", headers=CATALOG_HEADERS
        )

        assert response.status_code == 200
        skus = {sku["name"]: sku for sku in response.json()["skus"]}
        blue = skus["Синяя L"]
        assert blue["active_quantity"] == 0
        assert blue["in_stock"] is False


class TestProductCardUnhappyPath:
    @pytest.mark.asyncio
    async def test_blocked_product_returns_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        blocked = await _seed_card_product(session, test_category, status=ProductStatus.BLOCKED)
        deleted = await _seed_card_product(session, test_category, deleted=True)
        not_moderated = await _seed_card_product(
            session, test_category, status=ProductStatus.ON_MODERATION
        )
        missing_id = uuid.uuid4()

        for product_id in (blocked.id, deleted.id, not_moderated.id, missing_id):
            response = await client.get(
                f"/api/v1/catalog/products/{product_id}", headers=CATALOG_HEADERS
            )
            assert response.status_code == 404
            assert response.json()["code"] == "NOT_FOUND"

    @pytest.mark.asyncio
    async def test_requires_service_key(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        product = await _seed_card_product(session, test_category)

        response = await client.get(f"/api/v1/catalog/products/{product.id}")

        assert response.status_code == 401
