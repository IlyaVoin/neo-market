import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SKU, Product, ProductModeration, ProductStatus
from src.models.enums import ModerationStatus
from src.services.event_publisher import EventPublisher


class ModerationCardNotFoundError(Exception):
    """Карточка модерации не найдена → 404."""


class NotAssignedError(Exception):
    """Карточка закреплена за другим модератором → 403."""


class ApproveConflictError(Exception):
    """Статус/условия не допускают approve → 409."""


class ModerationApprovalService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.event_publisher = EventPublisher(session)

    async def _get_card(self, product_id: uuid.UUID) -> ProductModeration | None:
        stmt = select(ProductModeration).where(ProductModeration.product_id == product_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def _product_sku_count(self, product_id: uuid.UUID) -> int:
        stmt = select(func.count(SKU.id)).where(SKU.product_id == product_id)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def approve_product(
        self,
        product_id: uuid.UUID,
        moderator_id: uuid.UUID,
        comment: str | None = None,
    ) -> ProductModeration:
        card = await self._get_card(product_id)
        if card is None:
            raise ModerationCardNotFoundError("Product not found in moderation queue")

        if card.status == ModerationStatus.HARD_BLOCKED:
            raise ApproveConflictError("Product is permanently blocked")
        if card.status != ModerationStatus.IN_REVIEW:
            raise ApproveConflictError("Product is not in review status")
        if card.moderator_id != moderator_id:
            raise NotAssignedError("This moderation card is not assigned to you")

        if await self._product_sku_count(product_id) == 0:
            raise ApproveConflictError("Product has no SKUs, cannot approve")

        card.status = ModerationStatus.MODERATED
        card.date_moderation = datetime.now(UTC)
        card.moderator_comment = comment
        card.blocking_reason_id = None
        card.field_reports = []

        if card.event_idempotency_key is None:
            card.event_idempotency_key = uuid.uuid4()
        await self.session.flush()

        await self.event_publisher.publish_moderated_to_b2b(
            product_id, card.event_idempotency_key
        )

        # B2B в этом монорепо — тот же процесс: применяем эффект события, чтобы
        # товар стал видимым в каталоге (MODERATED).
        product = await self.session.get(Product, product_id)
        if product is not None:
            product.status = ProductStatus.MODERATED
            product.blocking_reason_id = None
            product.blocking_reason_title = None
            product.moderator_comment = None
            product.field_reports = []

        await self.session.flush()
        return card
