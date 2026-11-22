import uuid

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.models import SKU, Category, Product, ProductStatus
from src.models.enums import OrderStatus
from src.models.order import Order


def _token(user_id: uuid.UUID) -> str:
    return jwt.encode(
        {"sub": str(user_id)}, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_product(session: AsyncSession, category: Category) -> Product:
    product = Product(
        seller_id=uuid.uuid4(),
        category_id=category.id,
        title="iPhone 15 Pro Max",
        description="Flagship",
        slug=f"iphone-{uuid.uuid4().hex[:6]}",
        status=ProductStatus.MODERATED,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def _seed_sku(
    session: AsyncSession, product: Product, *, active_quantity: int = 10
) -> SKU:
    sku = SKU(
        product_id=product.id,
        name="256GB Black",
        price=1000,
        cost_price=900,
        discount=0,
        image="/s3/p.jpg",
        active_quantity=active_quantity,
        reserved_quantity=0,
    )
    session.add(sku)
    await session.commit()
    await session.refresh(sku)
    return sku


async def _checkout(
    client: AsyncClient, token: str, sku_id: uuid.UUID, quantity: int
) -> dict:
    response = await client.post(
        "/api/v1/orders",
        headers={**_auth(token), "Idempotency-Key": str(uuid.uuid4())},
        json={"items": [{"sku_id": str(sku_id), "quantity": quantity}]},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _get_order(session: AsyncSession, order_id: uuid.UUID) -> Order:
    result = await session.execute(
        select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    )
    return result.scalar_one()


class TestCancelHappyPath:
    @pytest.mark.asyncio
    async def test_cancel_paid_order_transitions_to_cancelled(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        order = await _checkout(client, valid_token, sku.id, 2)
        order_id = uuid.UUID(order["id"])

        await session.refresh(sku)
        assert sku.active_quantity == 8
        assert sku.reserved_quantity == 2

        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel", headers=_auth(valid_token)
        )

        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"

        # Резерв освобождён.
        await session.refresh(sku)
        assert sku.active_quantity == 10
        assert sku.reserved_quantity == 0

    @pytest.mark.asyncio
    async def test_cancel_freezes_order_status(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=5)
        order = await _checkout(client, valid_token, sku.id, 1)
        order_id = uuid.UUID(order["id"])

        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel", headers=_auth(valid_token)
        )
        assert response.status_code == 200

        stored = await _get_order(session, order_id)
        assert stored.status == OrderStatus.CANCELLED


class TestCancelUnhappyPath:
    @pytest.mark.asyncio
    async def test_unreserve_failure_transitions_to_cancel_pending(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        order = await _checkout(client, valid_token, sku.id, 2)
        order_id = uuid.UUID(order["id"])

        await session.refresh(sku)
        assert sku.active_quantity == 8
        assert sku.reserved_quantity == 2

        monkeypatch.setattr(settings, "b2b_unavailable", True)
        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel", headers=_auth(valid_token)
        )

        assert response.status_code == 200
        assert response.json()["status"] == "CANCEL_PENDING"

        # Резерв НЕ тронут (освободится при async retry).
        await session.refresh(sku)
        assert sku.active_quantity == 8
        assert sku.reserved_quantity == 2

    @pytest.mark.asyncio
    async def test_cancel_assembling_order_returns_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        order = await _checkout(client, valid_token, sku.id, 1)
        order_id = uuid.UUID(order["id"])

        stored = await _get_order(session, order_id)
        stored.status = OrderStatus.ASSEMBLING
        await session.commit()

        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel", headers=_auth(valid_token)
        )

        assert response.status_code == 409
        data = response.json()
        assert data["code"] == "CANCEL_NOT_ALLOWED"
        assert data["current_status"] == "ASSEMBLING"

    @pytest.mark.asyncio
    async def test_other_user_order_returns_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        order = await _checkout(client, valid_token, sku.id, 1)
        order_id = uuid.UUID(order["id"])

        other_token = _token(uuid.uuid4())
        response = await client.post(
            f"/api/v1/orders/{order_id}/cancel", headers=_auth(other_token)
        )

        assert response.status_code == 404
        assert response.json()["code"] == "ORDER_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_cancel_requires_auth_returns_401(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        order = await _checkout(client, valid_token, sku.id, 1)
        order_id = uuid.UUID(order["id"])

        response = await client.post(f"/api/v1/orders/{order_id}/cancel")

        assert response.status_code == 401
