import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import SKU, Category, Product, ProductStatus
from src.models.order import Order, OrderItem


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_product(
    session: AsyncSession,
    category: Category,
    *,
    title: str = "iPhone 15 Pro Max",
    status: ProductStatus = ProductStatus.MODERATED,
    deleted: bool = False,
) -> Product:
    product = Product(
        seller_id=uuid.uuid4(),
        category_id=category.id,
        title=title,
        description="Flagship",
        slug=f"{title}-{uuid.uuid4().hex[:6]}",
        status=status,
        deleted=deleted,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def _seed_sku(
    session: AsyncSession,
    product: Product,
    *,
    name: str = "256GB Black",
    price: int = 1000,
    active_quantity: int = 10,
) -> SKU:
    sku = SKU(
        product_id=product.id,
        name=name,
        price=price,
        cost_price=price - 100,
        discount=0,
        image="/s3/p.jpg",
        active_quantity=active_quantity,
        reserved_quantity=0,
    )
    session.add(sku)
    await session.commit()
    await session.refresh(sku)
    return sku


async def _count_orders(session: AsyncSession) -> int:
    result = await session.execute(select(func.count(Order.id)))
    return int(result.scalar_one())


class TestCheckoutHappyPath:
    @pytest.mark.asyncio
    async def test_checkout_creates_paid_order_with_fixed_prices(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku1 = await _seed_sku(session, product, name="256GB Black", price=12999000, active_quantity=5)
        sku2 = await _seed_sku(session, product, name="512GB White", price=14999000, active_quantity=3)
        key = str(uuid.uuid4())

        response = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": key},
            json={
                "items": [
                    {"sku_id": str(sku1.id), "quantity": 2},
                    {"sku_id": str(sku2.id), "quantity": 1},
                ],
                "delivery_address": "г. Екатеринбург, ул. Мира 19",
            },
        )

        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "PAID"
        assert data["buyer_id"]
        assert data["total_amount"] == 12999000 * 2 + 14999000
        assert data["subtotal"] == data["total_amount"]

        items = {i["sku_id"]: i for i in data["items"]}
        assert items[str(sku1.id)]["unit_price"] == 12999000
        assert items[str(sku1.id)]["line_total"] == 12999000 * 2
        assert items[str(sku1.id)]["product_title"] == "iPhone 15 Pro Max"
        assert items[str(sku1.id)]["sku_name"] == "256GB Black"

        # Резерв списан со склада.
        await session.refresh(sku1)
        await session.refresh(sku2)
        assert sku1.active_quantity == 3
        assert sku1.reserved_quantity == 2

    @pytest.mark.asyncio
    async def test_checkout_freezes_price_even_if_sku_price_changes(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, price=1000, active_quantity=5)
        key = str(uuid.uuid4())

        response = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": key},
            json={"items": [{"sku_id": str(sku.id), "quantity": 1}]},
        )
        assert response.status_code == 201
        assert response.json()["items"][0]["unit_price"] == 1000

        # Продавец поднимает цену уже после покупки.
        sku.price = 5000
        await session.commit()

        result = await session.execute(
            select(OrderItem.unit_price).where(OrderItem.sku_id == sku.id)
        )
        assert result.scalar_one() == 1000


class TestCheckoutUnhappyPath:
    @pytest.mark.asyncio
    async def test_partial_reserve_failure_returns_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        ok_sku = await _seed_sku(session, product, name="ok", active_quantity=5)
        empty_sku = await _seed_sku(session, product, name="empty", active_quantity=0)
        ok_id, empty_id = ok_sku.id, empty_sku.id
        key = str(uuid.uuid4())

        response = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": key},
            json={
                "items": [
                    {"sku_id": str(ok_id), "quantity": 2},
                    {"sku_id": str(empty_id), "quantity": 1},
                ]
            },
        )

        assert response.status_code == 409
        data = response.json()
        assert data["code"] == "RESERVE_FAILED"
        failed_ids = {f["sku_id"] for f in data["failed_items"]}
        assert str(empty_id) in failed_ids

        # All-or-nothing: запас успешного SKU не изменился, заказа нет.
        await session.refresh(ok_sku)
        assert ok_sku.active_quantity == 5
        assert ok_sku.reserved_quantity == 0
        assert await _count_orders(session) == 0

    @pytest.mark.asyncio
    async def test_idempotency_returns_existing_order(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        key = str(uuid.uuid4())
        body = {"items": [{"sku_id": str(sku.id), "quantity": 2}]}

        first = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": key},
            json=body,
        )
        assert first.status_code == 201
        first_order_id = first.json()["id"]

        second = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": key},
            json=body,
        )
        assert second.status_code == 201
        assert second.json()["id"] == first_order_id

        # Ровно один заказ, склад списан один раз.
        assert await _count_orders(session) == 1
        await session.refresh(sku)
        assert sku.active_quantity == 8
        assert sku.reserved_quantity == 2

    @pytest.mark.asyncio
    async def test_b2b_unavailable_returns_503(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        monkeypatch: pytest.MonkeyPatch,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product, active_quantity=10)
        monkeypatch.setattr(settings, "b2b_unavailable", True)

        response = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": str(uuid.uuid4())},
            json={"items": [{"sku_id": str(sku.id), "quantity": 1}]},
        )

        assert response.status_code == 503
        assert response.json()["code"] == "B2B_UNAVAILABLE"
        assert await _count_orders(session) == 0


class TestCheckoutValidation:
    @pytest.mark.asyncio
    async def test_checkout_requires_idempotency_key_returns_400(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product)

        response = await client.post(
            "/api/v1/orders",
            headers=_auth(valid_token),
            json={"items": [{"sku_id": str(sku.id), "quantity": 1}]},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_checkout_empty_items_returns_400(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        response = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": str(uuid.uuid4())},
            json={"items": []},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "INVALID_REQUEST"

    @pytest.mark.asyncio
    async def test_checkout_quantity_below_one_returns_422(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product)

        response = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": str(uuid.uuid4())},
            json={"items": [{"sku_id": str(sku.id), "quantity": 0}]},
        )
        assert response.status_code == 422
        assert response.json()["code"] == "INVALID_QUANTITY"

    @pytest.mark.asyncio
    async def test_checkout_requires_auth_returns_401(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
    ):
        response = await client.post(
            "/api/v1/orders",
            headers={"Idempotency-Key": str(uuid.uuid4())},
            json={"items": [{"sku_id": str(uuid.uuid4()), "quantity": 1}]},
        )
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_checkout_body_mismatch_returns_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        sku = await _seed_sku(session, product)
        key = str(uuid.uuid4())

        first = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": key},
            json={"items": [{"sku_id": str(sku.id), "quantity": 1}]},
        )
        assert first.status_code == 201

        second = await client.post(
            "/api/v1/orders",
            headers={**_auth(valid_token), "Idempotency-Key": key},
            json={"items": [{"sku_id": str(sku.id), "quantity": 5}]},
        )
        assert second.status_code == 409
        assert second.json()["code"] == "INVALID_REQUEST"
