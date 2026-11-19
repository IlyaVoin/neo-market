import uuid

import pytest
from httpx import AsyncClient

from src.models import Category


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
