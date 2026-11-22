import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import (
    SKU,
    Category,
    OutboxEvent,
    Product,
    ProductBlockingReason,
    ProductModeration,
    ProductStatus,
)
from src.models.enums import ModerationStatus


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_product(
    session: AsyncSession,
    category: Category,
    seller_id: uuid.UUID,
    *,
    status: ProductStatus = ProductStatus.ON_MODERATION,
) -> Product:
    product = Product(
        seller_id=seller_id,
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
    moderator_id: uuid.UUID,
    *,
    status: ModerationStatus = ModerationStatus.IN_REVIEW,
) -> ProductModeration:
    card = ProductModeration(product_id=product.id, status=status, moderator_id=moderator_id)
    session.add(card)
    await session.commit()
    await session.refresh(card)
    return card


async def _seed_reason(
    session: AsyncSession, *, hard_block: bool = True, code: str = "COUNTERFEIT"
) -> ProductBlockingReason:
    reason = ProductBlockingReason(
        code=f"{code}-{uuid.uuid4().hex[:6]}", title="Контрафактный товар", hard_block=hard_block
    )
    session.add(reason)
    await session.commit()
    await session.refresh(reason)
    return reason


async def _blocked_events(session: AsyncSession) -> list[OutboxEvent]:
    result = await session.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "moderation.blocked")
    )
    return list(result.scalars().all())


class TestHardBlockHappyPath:
    @pytest.mark.asyncio
    async def test_hard_block_transitions_to_terminal_and_emits_event(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, seller_id_from_token)
        reason = await _seed_reason(session, hard_block=True)

        response = await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id), "moderator_comment": "контрафакт"},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "HARD_BLOCKED"

        card = (
            await session.execute(
                select(ProductModeration).where(ProductModeration.product_id == product.id)
            )
        ).scalar_one()
        assert card.status == ModerationStatus.HARD_BLOCKED

        await session.refresh(product)
        assert product.status == ProductStatus.HARD_BLOCKED

        events = await _blocked_events(session)
        assert len(events) == 1
        assert events[0].payload["status"] == "BLOCKED"

    @pytest.mark.asyncio
    async def test_hard_block_event_carries_hard_block_true(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, seller_id_from_token)
        reason = await _seed_reason(session, hard_block=True)

        response = await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )
        assert response.status_code == 200

        events = await _blocked_events(session)
        assert len(events) == 1
        assert events[0].payload["hard_block"] is True
        assert events[0].payload["blocking_reason"]["id"] == str(reason.id)


class TestHardBlockTerminalProtection:
    @pytest.mark.asyncio
    async def test_any_modify_on_hard_blocked_returns_403(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, seller_id_from_token)
        reason = await _seed_reason(session, hard_block=True)

        decline = await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )
        assert decline.status_code == 200

        product_id = product.id
        # Каждый неуспешный запрос делает rollback, который истекает инстансы
        # в общей тестовой сессии; очищаем identity-map перед следующим запросом.
        session.expunge_all()

        patch = await client.patch(
            f"/api/v1/products/{product_id}",
            headers=_auth(valid_token),
            json={"title": "new"},
        )
        assert patch.status_code == 403

        session.expunge_all()
        delete = await client.delete(
            f"/api/v1/products/{product_id}", headers=_auth(valid_token)
        )
        assert delete.status_code == 403

        session.expunge_all()
        add_sku = await client.post(
            "/api/v1/skus",
            headers=_auth(valid_token),
            json={
                "product_id": str(product_id),
                "name": "x",
                "price": 1,
                "cost_price": 1,
                "image": "/s3/x.jpg",
            },
        )
        assert add_sku.status_code == 403


class TestHardBlockIncomingEvents:
    @pytest.mark.asyncio
    async def test_edited_event_on_hard_blocked_is_ignored(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        card = await _seed_card(session, product, seller_id_from_token)
        reason = await _seed_reason(session, hard_block=True)
        await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )

        response = await client.post(
            "/api/v1/events/product",
            headers={"X-Service-Key": settings.b2b_to_mod_key},
            json={
                "product_id": str(product.id),
                "seller_id": str(uuid.uuid4()),
                "event": "EDITED",
            },
        )

        assert response.status_code == 200
        await session.refresh(card)
        assert card.status == ModerationStatus.HARD_BLOCKED

    @pytest.mark.asyncio
    async def test_deleted_event_removes_hard_blocked(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, seller_id_from_token)
        reason = await _seed_reason(session, hard_block=True)
        await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )

        response = await client.post(
            "/api/v1/events/product",
            headers={"X-Service-Key": settings.b2b_to_mod_key},
            json={
                "product_id": str(product.id),
                "seller_id": str(uuid.uuid4()),
                "event": "DELETED",
            },
        )

        assert response.status_code == 200
        result = await session.execute(
            select(func.count(ProductModeration.id)).where(
                ProductModeration.product_id == product.id
            )
        )
        assert int(result.scalar_one()) == 0


class TestDeclineValidation:
    @pytest.mark.asyncio
    async def test_soft_block_returns_blocked(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, seller_id_from_token)
        reason = await _seed_reason(session, hard_block=False, code="SOFT")

        response = await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "BLOCKED"
        events = await _blocked_events(session)
        assert events[0].payload["hard_block"] is False

    @pytest.mark.asyncio
    async def test_decline_hard_blocked_returns_409(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, seller_id_from_token)
        reason = await _seed_reason(session, hard_block=True)
        await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )

        again = await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )
        assert again.status_code == 409

    @pytest.mark.asyncio
    async def test_decline_others_card_returns_403(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, uuid.uuid4())
        reason = await _seed_reason(session, hard_block=True)

        response = await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(reason.id)},
        )
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_decline_unknown_reason_returns_400(
        self,
        client: AsyncClient,
        session: AsyncSession,
        test_category: Category,
        valid_token: str,
        seller_id_from_token: uuid.UUID,
    ):
        product = await _seed_product(session, test_category, seller_id_from_token)
        await _seed_sku(session, product)
        await _seed_card(session, product, seller_id_from_token)

        response = await client.post(
            f"/api/v1/products/{product.id}/decline",
            headers=_auth(valid_token),
            json={"blocking_reason_id": str(uuid.uuid4())},
        )
        assert response.status_code == 400
