import uuid

import pytest
from httpx import AsyncClient

from src.models import Category


class TestValidation:
    @pytest.mark.asyncio
    async def test_title_too_long_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
    ):
        long_title = "a" * 256
        response = await client.post(
            "/api/v1/products",
            json={
                "title": long_title,
                "description": "Test Description",
                "category_id": str(test_category.id),
                "images": [{"url": "/s3/test.jpg", "ordering": 0}],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        assert "title must be 1-255 characters" in data["message"]

    @pytest.mark.asyncio
    async def test_invalid_category_uuid_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
    ):
        response = await client.post(
            "/api/v1/products",
            json={
                "title": "Test Product",
                "description": "Test Description",
                "category_id": "not-a-uuid",
                "images": [{"url": "/s3/test.jpg", "ordering": 0}],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        assert "category_id must be a valid UUID" in data["message"]

    @pytest.mark.asyncio
    async def test_category_not_found_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
    ):
        non_existent_category = uuid.uuid4()
        response = await client.post(
            "/api/v1/products",
            json={
                "title": "Test Product",
                "description": "Test Description",
                "category_id": str(non_existent_category),
                "images": [{"url": "/s3/test.jpg", "ordering": 0}],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        assert data["message"] == "Category not found"

    @pytest.mark.asyncio
    async def test_missing_title_returns_400(
        self,
        client: AsyncClient,
        valid_token: str,
        test_category: Category,
    ):
        response = await client.post(
            "/api/v1/products",
            json={
                "description": "Test Description",
                "category_id": str(test_category.id),
                "images": [{"url": "/s3/test.jpg", "ordering": 0}],
            },
            headers={"Authorization": f"Bearer {valid_token}"},
        )

        assert response.status_code == 400
        data = response.json()
        assert data["code"] == "INVALID_REQUEST"
        assert "title is required" in data["message"]

    @pytest.mark.asyncio
    async def test_unauthorized_returns_401(
        self,
        client: AsyncClient,
        test_category: Category,
    ):
        response = await client.post(
            "/api/v1/products",
            json={
                "title": "Test Product",
                "description": "Test Description",
                "category_id": str(test_category.id),
                "images": [{"url": "/s3/test.jpg", "ordering": 0}],
            },
        )

        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "UNAUTHORIZED"
