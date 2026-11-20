import uuid

import pytest
from httpx import AsyncClient
from jose import jwt

from src.config import settings
from src.models import SKU, Category, Product, ProductStatus


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
