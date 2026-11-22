import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    SKU,
    Category,
    OutboxEvent,
    Product,
    ProductModeration,
    ProductStatus,
)
from src.models.enums import ModerationStatus


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_product(
    session: AsyncSession, category: Category, *, status: ProductStatus = ProductStatus.ON_MODERATION
) -> Product:
    product = Product(
        seller_id=uuid.uuid4(),
        category_id=category.id,
        title="iPhone 15 Pro Max",
        description="Flagship",
        slug=f"iphone-{uuid.uuid4().hex[:6]}",
        status=status,
    )
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


async def _seed_sku(session: AsyncSession, product: Product) -> SKU:
    sku = SKU(
        product_id=product.id,
        name="256GB Black",
        price=1000,
        cost_price=900,
        discount=0,
        image="/s3/p.jpg",
        active_quantity=5,
        reserved_quantity=0,
    )
    session.add(sku)
    await session.commit()
    await session.refresh(sku)
    return sku


async def _seed_card(
    session: AsyncSession,
    product: Product,
    *,
    moderator_id: uuid.UUID,
    status: ModerationStatus = ModerationStatus.IN_REVIEW,
) -> ProductModeration:
    card = ProductModeration(
        product_id=product.id,
        status=status,
        moderator_id=moderator_id,
    )
    session.add(card)
    await session.commit()
    await session.refresh(card)
    return card


async def _count_moderated_events(session: AsyncSession) -> int:
    result = await session.execute(
        select(func.count(OutboxEvent.id)).where(
            OutboxEvent.event_type == "moderation.moderated"
        )
    )
    return int(result.scalar_one())


class TestApproveHappyPath:
    @pytest.mark.asyncio
    async def test_approve_transitions_to_moderated_and_emits_event(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category)
        await _seed_sku(session, product)
        await _seed_card(session, product, moderator_id=seller_id_from_token)

        response = await client.post(
            f"/api/v1/products/{product.id}/approve",
            headers=_auth(valid_token),
            json={"moderator_comment": "ok"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "MODERATED"

        # Карточка → MODERATED.
        card = (
            await session.execute(
                select(ProductModeration).where(
                    ProductModeration.product_id == product.id
                )
            )
        ).scalar_one()
        assert card.status == ModerationStatus.MODERATED
        assert card.event_idempotency_key is not None

        # Событие MODERATED ушло в B2B (outbox).
        assert await _count_moderated_events(session) == 1

        # Товар стал MODERATED (виден в каталоге).
        await session.refresh(product)
        assert product.status == ProductStatus.MODERATED


class TestApproveUnhappyPath:
    @pytest.mark.asyncio
    async def test_approve_others_card_returns_403(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)
        await _seed_sku(session, product)
        await _seed_card(session, product, moderator_id=uuid.uuid4())

        response = await client.post(
            f"/api/v1/products/{product.id}/approve", headers=_auth(valid_token)
        )

        assert response.status_code == 403
        assert await _count_moderated_events(session) == 0

    @pytest.mark.asyncio
    async def test_approve_after_edited_returns_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category)
        await _seed_sku(session, product)
        card = await _seed_card(session, product, moderator_id=seller_id_from_token)
        # Продавец отредактировал во время review → карточка вернулась в PENDING.
        card.status = ModerationStatus.PENDING
        await session.commit()

        response = await client.post(
            f"/api/v1/products/{product.id}/approve", headers=_auth(valid_token)
        )

        assert response.status_code == 409
        assert await _count_moderated_events(session) == 0

    @pytest.mark.asyncio
    async def test_approve_without_sku_returns_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category)
        await _seed_card(session, product, moderator_id=seller_id_from_token)

        response = await client.post(
            f"/api/v1/products/{product.id}/approve", headers=_auth(valid_token)
        )

        assert response.status_code == 409
        assert await _count_moderated_events(session) == 0


class TestApproveValidation:
    @pytest.mark.asyncio
    async def test_approve_hard_blocked_returns_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, status=ProductStatus.HARD_BLOCKED)
        await _seed_sku(session, product)
        await _seed_card(
            session,
            product,
            moderator_id=seller_id_from_token,
            status=ModerationStatus.HARD_BLOCKED,
        )

        response = await client.post(
            f"/api/v1/products/{product.id}/approve", headers=_auth(valid_token)
        )

        assert response.status_code == 409

    @pytest.mark.asyncio
    async def test_approve_missing_card_returns_404(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
    ):
        product = await _seed_product(session, test_category)

        response = await client.post(
            f"/api/v1/products/{product.id}/approve", headers=_auth(valid_token)
        )

        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_approve_requires_auth_returns_401(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category)
        await _seed_sku(session, product)
        await _seed_card(session, product, moderator_id=seller_id_from_token)

        response = await client.post(f"/api/v1/products/{product.id}/approve")

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_approve_twice_emits_single_event(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category)
        await _seed_sku(session, product)
        await _seed_card(session, product, moderator_id=seller_id_from_token)

        first = await client.post(
            f"/api/v1/products/{product.id}/approve", headers=_auth(valid_token)
        )
        assert first.status_code == 200
        second = await client.post(
            f"/api/v1/products/{product.id}/approve", headers=_auth(valid_token)
        )
        assert second.status_code == 409

        # Повторный approve не порождает второе событие.
        assert await _count_moderated_events(session) == 1
