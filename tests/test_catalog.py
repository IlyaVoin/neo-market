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
    ProductStatus,
)

CATALOG_HEADERS = {"X-Service-Key": settings.b2b_to_b2c_key}


async def _seed_product(
    session: AsyncSession,
    category: Category,
    title: str,
    price: int,
    active_quantity: int = 5,
    status: ProductStatus = ProductStatus.MODERATED,
    deleted: bool = False,
    brand: str | None = None,
) -> Product:
    product = Product(
        seller_id=uuid.uuid4(),
        category_id=category.id,
        title=title,
        description="desc",
        slug=f"{title}-{uuid.uuid4().hex[:6]}",
        status=status,
        deleted=deleted,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)

    if brand is not None:
        session.add(
            ProductCharacteristic(product_id=product.id, name="Бренд", value=brand)
        )
        await session.commit()

    session.add(
        SKU(
            product_id=product.id,
            name=f"{title} SKU",
            price=price,
            cost_price=price - 1,
            discount=0,
            image="/s3/p.jpg",
            active_quantity=active_quantity,
            reserved_quantity=0,
        )
    )
    await session.commit()
    await session.refresh(product)
    return product


class TestCatalogHappyPath:
    @pytest.mark.asyncio
    async def test_catalog_returns_filtered_sorted_products(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        await _seed_product(session, test_category, "A Low", price=1000, brand="Apple")
        await _seed_product(session, test_category, "B High", price=9000, brand="Samsung")
        await _seed_product(session, test_category, "C Mid", price=5000, brand="Apple")
        # Скрытые — не должны попасть в выдачу.
        await _seed_product(
            session, test_category, "D Hidden", price=100, status=ProductStatus.BLOCKED
        )
        await _seed_product(
            session, test_category, "E Deleted", price=100, deleted=True
        )
        await _seed_product(
            session, test_category, "F Empty", price=100, active_quantity=0
        )
        cat_id = test_category.id

        # Фильтр по категории + бренд Apple + сортировка по цене возр.
        response = await client.get(
            "/api/v1/catalog/products",
            params={"category_id": str(cat_id), "filter[Бренд]": "Apple", "sort": "price_asc"},
            headers=CATALOG_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        titles = [item["title"] for item in data["items"]]
        assert titles == ["A Low", "C Mid"]
        assert data["items"][0]["price"] == 1000
        assert data["items"][0]["min_price"] == 1000
        assert data["items"][0]["in_stock"] is True
        assert data["total_count"] == 2

        # Сортировка по убыванию цены.
        desc = await client.get(
            "/api/v1/catalog/products",
            params={"category_id": str(cat_id), "sort": "price_desc"},
            headers=CATALOG_HEADERS,
        )
        prices = [item["price"] for item in desc.json()["items"]]
        # Только MODERATED + не удалённые + in-stock: A,C + B High(9000), C Mid(5000), A Low(1000)
        assert prices == sorted(prices, reverse=True)
        assert 9000 in prices

        # Пагинация.
        page = await client.get(
            "/api/v1/catalog/products",
            params={"category_id": str(cat_id), "sort": "price_asc", "limit": 1, "offset": 1},
            headers=CATALOG_HEADERS,
        )
        assert page.status_code == 200
        assert len(page.json()["items"]) == 1
        assert page.json()["items"][0]["title"] == "C Mid"
        assert page.json()["total_count"] == 3

    @pytest.mark.asyncio
    async def test_facets_return_counts_per_filter_value(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        await _seed_product(session, test_category, "A", price=1000, brand="Apple")
        await _seed_product(session, test_category, "B", price=2000, brand="Apple")
        await _seed_product(session, test_category, "C", price=3000, brand="Samsung")
        cat_id = test_category.id

        response = await client.get(
            "/api/v1/catalog/facets",
            params={"category_id": str(cat_id)},
            headers=CATALOG_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        facets = {f["name"]: f["values"] for f in data["facets"]}
        brand_values = {v["value"]: v["count"] for v in facets["Бренд"]}
        assert brand_values == {"Apple": 2, "Samsung": 1}


class TestCatalogEdgeCases:
    @pytest.mark.asyncio
    async def test_invalid_sort_returns_400(self, client: AsyncClient):
        response = await client.get(
            "/api/v1/catalog/products",
            params={"sort": "totally_unknown"},
            headers=CATALOG_HEADERS,
        )
        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        for allowed in [
            "rating",
            "popularity",
            "price_asc",
            "price_desc",
            "date_desc",
            "discount_desc",
        ]:
            assert allowed in data["message"]

    @pytest.mark.asyncio
    async def test_b2b_unavailable_returns_502(
        self, client: AsyncClient, monkeypatch
    ):
        monkeypatch.setattr(settings, "catalog_b2b_unavailable", True)
        response = await client.get(
            "/api/v1/catalog/products", headers=CATALOG_HEADERS
        )
        assert response.status_code == 502
        assert response.json()["code"] == "BAD_GATEWAY"

    @pytest.mark.asyncio
    async def test_catalog_missing_service_key_returns_401(self, client: AsyncClient):
        response = await client.get("/api/v1/catalog/products")
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_category_returns_empty(
        self, client: AsyncClient, test_category: Category
    ):
        response = await client.get(
            "/api/v1/catalog/products",
            params={"category_id": str(test_category.id)},
            headers=CATALOG_HEADERS,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total_count"] == 0
