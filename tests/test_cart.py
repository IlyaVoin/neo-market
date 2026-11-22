import uuid

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import SKU, CartItem, Category, Product, ProductStatus


def _buyer_headers(user_id: uuid.UUID) -> dict:
    token = jwt.encode(
        {"sub": str(user_id)}, settings.jwt_secret, algorithm=settings.jwt_algorithm
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_sku(
    session: AsyncSession,
    category: Category,
    active_quantity: int,
    price: int = 12999000,
) -> SKU:
    product = Product(
        seller_id=uuid.uuid4(),
        category_id=category.id,
        title="iPhone 15 Pro Max",
        description="Flagship",
        slug=f"iphone-{uuid.uuid4().hex[:8]}",
        status=ProductStatus.MODERATED,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)

    sku = SKU(
        product_id=product.id,
        name="256GB Black",
        price=price,
        cost_price=9500000,
        discount=0,
        image="/s3/iphone15-black-256.jpg",
        active_quantity=active_quantity,
        reserved_quantity=0,
    )
    session.add(sku)
    await session.commit()
    await session.refresh(sku)
    return sku


class TestCartHappyPath:
    @pytest.mark.asyncio
    async def test_add_sku_increments_quantity_if_already_in_cart(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        sku = await _seed_sku(session, test_category, active_quantity=10)
        sku_id = str(sku.id)
        headers = {"X-Session-Id": str(uuid.uuid4())}

        first = await client.post(
            "/api/v1/cart/items", json={"sku_id": sku_id, "quantity": 2}, headers=headers
        )
        assert first.status_code == 201
        assert first.json()["quantity"] == 2

        second = await client.post(
            "/api/v1/cart/items", json={"sku_id": sku_id, "quantity": 3}, headers=headers
        )
        assert second.status_code == 200
        assert second.json()["quantity"] == 5

        cart = await client.get("/api/v1/cart", headers=headers)
        items = cart.json()["items"]
        assert len(items) == 1
        assert items[0]["quantity"] == 5

    @pytest.mark.asyncio
    async def test_get_cart_enriched_with_b2b_data(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        sku = await _seed_sku(session, test_category, active_quantity=10, price=5000000)
        sku_id = str(sku.id)
        headers = {"X-Session-Id": str(uuid.uuid4())}

        await client.post(
            "/api/v1/cart/items", json={"sku_id": sku_id, "quantity": 3}, headers=headers
        )

        cart = await client.get("/api/v1/cart", headers=headers)
        assert cart.status_code == 200
        data = cart.json()
        item = data["items"][0]
        # Данные берутся из B2B, не из БД корзины.
        assert item["title"] == "256GB Black"
        assert item["price"] == 5000000
        assert item["image"] == "/s3/iphone15-black-256.jpg"
        assert item["available"] is True
        assert item["unavailable_reason"] is None
        assert item["line_total"] == 5000000 * 3
        assert data["summary"]["total_amount"] == 5000000 * 3
        assert data["summary"]["checkout_ready"] is True
        assert data["checkout_payload"]["items"] == [{"sku_id": sku_id, "quantity": 3}]


class TestCartUnavailable:
    @pytest.mark.asyncio
    async def test_unavailable_sku_shown_with_reason(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        sku = await _seed_sku(session, test_category, active_quantity=5)
        sku_id = str(sku.id)
        headers = {"X-Session-Id": str(uuid.uuid4())}

        await client.post(
            "/api/v1/cart/items", json={"sku_id": sku_id, "quantity": 2}, headers=headers
        )

        # Остаток упал до нуля после добавления.
        sku.active_quantity = 0
        await session.commit()

        cart = await client.get("/api/v1/cart", headers=headers)
        data = cart.json()
        item = data["items"][0]
        assert item["available"] is False
        assert item["unavailable_reason"] == "OUT_OF_STOCK"
        assert item["line_total"] == 0
        assert data["summary"]["total_amount"] == 0
        assert data["summary"]["unavailable_count"] == 1
        assert data["summary"]["checkout_ready"] is False
        assert data["checkout_payload"]["items"] == []


class TestCartMerge:
    @pytest.mark.asyncio
    async def test_guest_cart_merged_on_login(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        sku_a = await _seed_sku(session, test_category, active_quantity=10)
        sku_b = await _seed_sku(session, test_category, active_quantity=10)
        sku_a_id, sku_b_id = str(sku_a.id), str(sku_b.id)

        user_id = uuid.uuid4()
        session_id = str(uuid.uuid4())
        guest_headers = {"X-Session-Id": session_id}

        # Гость: sku_a x2, sku_b x1.
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": sku_a_id, "quantity": 2},
            headers=guest_headers,
        )
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": sku_b_id, "quantity": 1},
            headers=guest_headers,
        )

        # Авторизованный уже имеет sku_a x5.
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": sku_a_id, "quantity": 5},
            headers=_buyer_headers(user_id),
        )

        # Логин: JWT + X-Session-Id → merge.
        login_headers = {**_buyer_headers(user_id), "X-Session-Id": session_id}
        merged = await client.get("/api/v1/cart", headers=login_headers)
        assert merged.status_code == 200
        merged_items = {i["sku_id"]: i["quantity"] for i in merged.json()["items"]}
        assert merged_items[sku_a_id] == 5  # MAX(2, 5)
        assert merged_items[sku_b_id] == 1  # перенесён из гостевой

        # После merge гостевая корзина пуста, авторизованная — объединена.
        cart = await client.get("/api/v1/cart", headers=_buyer_headers(user_id))
        items = {i["sku_id"]: i["quantity"] for i in cart.json()["items"]}
        assert items == {sku_a_id: 5, sku_b_id: 1}

        guest_left = (
            await session.execute(select(CartItem).where(CartItem.session_id == session_id))
        ).scalars().all()
        assert guest_left == []


class TestCartEdgeCases:
    @pytest.mark.asyncio
    async def test_cross_identity_item_returns_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        sku = await _seed_sku(session, test_category, active_quantity=10)
        owner_headers = {"X-Session-Id": str(uuid.uuid4())}
        add = await client.post(
            "/api/v1/cart/items",
            json={"sku_id": str(sku.id), "quantity": 1},
            headers=owner_headers,
        )
        item_id = add.json()["item_id"]

        intruder_headers = {"X-Session-Id": str(uuid.uuid4())}
        response = await client.delete(
            f"/api/v1/cart/items/{item_id}", headers=intruder_headers
        )
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_clear_cart_empties(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        sku = await _seed_sku(session, test_category, active_quantity=10)
        headers = {"X-Session-Id": str(uuid.uuid4())}
        await client.post(
            "/api/v1/cart/items",
            json={"sku_id": str(sku.id), "quantity": 1},
            headers=headers,
        )

        cleared = await client.delete("/api/v1/cart", headers=headers)
        assert cleared.status_code == 204

        cart = await client.get("/api/v1/cart", headers=headers)
        assert cart.json()["items"] == []

    @pytest.mark.asyncio
    async def test_unauthenticated_returns_401(self, client: AsyncClient):
        response = await client.get("/api/v1/cart")
        assert response.status_code == 401
