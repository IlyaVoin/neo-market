import uuid

import pytest
from httpx import AsyncClient
from jose import jwt

from src.config import settings
from src.models import Category, Product, ProductStatus, SKU


class TestCreateProduct:
    @pytest.mark.asyncio
    async def test_create_product_returns_201_with_created_status(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
    ):
        response = await client.post(
            "/api/v1/products",
            json={
                "title": "iPhone 15 Pro Max",
                "description": "Latest Apple smartphone",
                "category_id": str(test_category.id),
                "images": [{"url": "/s3/iphone.jpg", "ordering": 0}],
                "characteristics": [{"name": "Brand", "value": "Apple"}],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "CREATED"
        assert data["skus"] == []
        assert data["deleted"] is False
        assert data["blocking_reason_id"] is None
        assert data["moderator_comment"] is None
        assert data["title"] == "iPhone 15 Pro Max"
        assert data["description"] == "Latest Apple smartphone"
        assert data["category_id"] == str(test_category.id)
        assert len(data["images"]) == 1
        assert data["images"][0]["url"] == "/s3/iphone.jpg"
        assert data["images"][0]["ordering"] == 0
        assert len(data["characteristics"]) == 1
        assert data["characteristics"][0]["name"] == "Brand"
        assert data["characteristics"][0]["value"] == "Apple"

    @pytest.mark.asyncio
    async def test_seller_id_taken_from_jwt(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
        seller_id_from_token: uuid.UUID,
    ):
        different_seller_id = uuid.uuid4()

        response = await client.post(
            "/api/v1/products",
            json={
                "title": "Test Product",
                "description": "Test Description",
                "category_id": str(test_category.id),
                "images": [{"url": "/s3/test.jpg", "ordering": 0}],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["seller_id"] == str(seller_id_from_token)
        assert data["seller_id"] != str(different_seller_id)

    @pytest.mark.asyncio
    async def test_missing_images_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
    ):
        response = await client.post(
            "/api/v1/products",
            json={
                "title": "Test Product",
                "description": "Test Description",
                "category_id": str(test_category.id),
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        assert data["message"] == "At least one image is required"

    @pytest.mark.asyncio
    async def test_missing_category_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
    ):
        response = await client.post(
            "/api/v1/products",
            json={
                "title": "Test Product",
                "description": "Test Description",
                "images": [{"url": "/s3/test.jpg", "ordering": 0}],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        assert data["message"] == "Category not found"


class TestGetProduct:
    @pytest.mark.asyncio
    async def test_get_moderated_product_returns_full_payload(
        self,
        client: AsyncClient,
        session,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
        test_category: Category,
    ):
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="iPhone 15 Pro Max",
            description="Latest Apple smartphone",
            status=ProductStatus.MODERATED,
        )
        session.add(product)
        await session.flush()

        sku = SKU(
            product_id=product.id,
            name="iPhone 15 Pro Max 256GB",
            price=120000,
            cost_price=95000,
            discount=5,
            image="/s3/iphone.jpg",
            active_quantity=10,
            reserved_quantity=2,
        )
        session.add(sku)
        await session.commit()

        response = await client.get(
            f"/api/v1/products/{product.id}",
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "MODERATED"
        assert data["blocked"] is False
        assert data["blocking_reason"] is None
        assert data["field_reports"] == []
        assert len(data["skus"]) == 1
        sku_data = data["skus"][0]
        assert sku_data["cost_price"] == 95000
        assert sku_data["reserved_quantity"] == 2

    @pytest.mark.asyncio
    async def test_get_blocked_product_returns_blocking_reason_and_field_reports(
        self,
        client: AsyncClient,
        session,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
        test_category: Category,
    ):
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Blocked Product",
            description="Some description",
            status=ProductStatus.BLOCKED,
            blocking_reason_id=uuid.uuid4(),
            moderator_comment="Trademark violation",
        )
        session.add(product)
        await session.commit()

        response = await client.get(
            f"/api/v1/products/{product.id}",
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "BLOCKED"
        assert data["blocked"] is True
        assert data["blocking_reason"] is not None
        assert data["blocking_reason"]["title"] == "Trademark violation"
        assert data["blocking_reason"]["comment"] == "Trademark violation"
        assert data["field_reports"] == []

    @pytest.mark.asyncio
    async def test_get_others_product_returns_404(
        self,
        client: AsyncClient,
        session,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
        test_category: Category,
    ):
        product = Product(
            seller_id=seller_id_from_token,
            category_id=test_category.id,
            title="Other Seller Product",
            description="Some description",
        )
        session.add(product)
        await session.commit()

        other_token = create_other_token()
        response = await client.get(
            f"/api/v1/products/{product.id}",
            headers={"Authorization": f"Bearer {other_token}"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"
        assert data["message"] == "Product not found"

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_404(
        self,
        client: AsyncClient,
        valid_token: str,
    ):
        response = await client.get(
            f"/api/v1/products/{uuid.uuid4()}",
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["code"] == "NOT_FOUND"
        assert data["message"] == "Product not found"


def create_other_token() -> str:
    return jwt.encode(
        {"sub": str(uuid.uuid4())},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def _make_product(
    session,
    seller_id,
    category_id,
    *,
    status=ProductStatus.MODERATED,
    deleted=False,
    title="Catalog Product",
):
    product = Product(
        seller_id=seller_id,
        category_id=category_id,
        title=title,
        description="Description",
        status=status,
        deleted=deleted,
    )
    session.add(product)
    return product


async def _flush(session):
    await session.flush()


def _make_sku(session, product, *, active_quantity=10, name="SKU", price=1000):
    sku = SKU(
        product_id=product.id,
        name=name,
        price=price,
        cost_price=800,
        discount=0,
        image="/s3/sku.jpg",
        active_quantity=active_quantity,
        reserved_quantity=0,
    )
    session.add(sku)
    return sku


class TestCatalogB2C:
    @pytest.mark.asyncio
    async def test_catalog_returns_moderated_in_stock_products(
        self,
        client: AsyncClient,
        session,
        seller_id_from_token: uuid.UUID,
        test_category: Category,
    ):
        p1 = _make_product(session, seller_id_from_token, test_category.id, title="In Stock")
        await session.flush()
        _make_sku(session, p1, active_quantity=5)
        p2 = _make_product(
            session, seller_id_from_token, test_category.id, status=ProductStatus.CREATED, title="Not Moderated"
        )
        await session.flush()
        _make_sku(session, p2, active_quantity=5)
        p3 = _make_product(
            session, seller_id_from_token, test_category.id, deleted=True, title="Deleted"
        )
        await session.flush()
        _make_sku(session, p3, active_quantity=5)
        p4 = _make_product(
            session, seller_id_from_token, test_category.id, title="Out of Stock"
        )
        await session.flush()
        _make_sku(session, p4, active_quantity=0)
        await session.commit()

        response = await client.get(
            "/api/v1/products",
            headers={"X-Service-Key": settings.b2b_to_b2c_key},
        )

        assert response.status_code == 200
        data = response.json()
        titles = [item["title"] for item in data["items"]]
        assert titles == ["In Stock"]
        assert data["total_count"] == 1

    @pytest.mark.asyncio
    async def test_catalog_excludes_hard_blocked(
        self,
        client: AsyncClient,
        session,
        seller_id_from_token: uuid.UUID,
        test_category: Category,
    ):
        p_ok = _make_product(session, seller_id_from_token, test_category.id, title="Visible")
        await session.flush()
        _make_sku(session, p_ok, active_quantity=3)
        p_blocked = _make_product(
            session,
            seller_id_from_token,
            test_category.id,
            status=ProductStatus.HARD_BLOCKED,
            title="Hard Blocked",
        )
        await session.flush()
        _make_sku(session, p_blocked, active_quantity=3)
        await session.commit()

        response = await client.get(
            "/api/v1/products",
            headers={"X-Service-Key": settings.b2b_to_b2c_key},
        )

        assert response.status_code == 200
        data = response.json()
        titles = [item["title"] for item in data["items"]]
        assert "Hard Blocked" not in titles
        assert titles == ["Visible"]

    @pytest.mark.asyncio
    async def test_catalog_missing_service_key_returns_401(self, client: AsyncClient):
        response = await client.get("/api/v1/products")
        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "UNAUTHORIZED"

    @pytest.mark.asyncio
    async def test_catalog_response_has_no_cost_price(
        self,
        client: AsyncClient,
        session,
        seller_id_from_token: uuid.UUID,
        test_category: Category,
    ):
        p = _make_product(session, seller_id_from_token, test_category.id, title="Safe Product")
        await session.flush()
        _make_sku(session, p, active_quantity=2)
        await session.commit()

        response = await client.get(
            "/api/v1/products",
            headers={"X-Service-Key": settings.b2b_to_b2c_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) == 1
        item = data["items"][0]
        assert "cost_price" not in item
        assert "reserved_quantity" not in item
        assert "seller_id" not in item
        assert "blocked" not in item
        sku = item["skus"][0]
        assert "cost_price" not in sku
        assert "reserved_quantity" not in sku

    @pytest.mark.asyncio
    async def test_catalog_batch_ids_returns_visible_subset(
        self,
        client: AsyncClient,
        session,
        seller_id_from_token: uuid.UUID,
        test_category: Category,
    ):
        p_visible = _make_product(session, seller_id_from_token, test_category.id, title="Visible")
        await session.flush()
        _make_sku(session, p_visible, active_quantity=4)
        p_hidden = _make_product(
            session,
            seller_id_from_token,
            test_category.id,
            status=ProductStatus.HARD_BLOCKED,
            title="Hidden",
        )
        await session.flush()
        _make_sku(session, p_hidden, active_quantity=4)
        await session.commit()

        ids_param = f"{p_visible.id},{p_hidden.id}"
        response = await client.get(
            "/api/v1/products",
            params={"ids": ids_param},
            headers={"X-Service-Key": settings.b2b_to_b2c_key},
        )

        assert response.status_code == 200
        data = response.json()
        titles = [item["title"] for item in data["items"]]
        assert titles == ["Visible"]
        assert data["total_count"] == 1
