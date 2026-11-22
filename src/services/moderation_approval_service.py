import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import (
    SKU,
    Product,
    ProductBlockingReason,
    ProductModeration,
    ProductStatus,
)
from src.models.enums import ModerationStatus
from src.schemas.moderation import DeclineRequest, IncomingProductEvent
from src.services.event_publisher import EventPublisher


class ModerationCardNotFoundError(Exception):
    """Карточка модерации не найдена → 404."""


class NotAssignedError(Exception):
    """Карточка закреплена за другим модератором → 403."""


class ApproveConflictError(Exception):
    """Статус/условия не допускают approve → 409."""


class BlockingReasonNotFoundError(Exception):
    """Причина блокировки не найдена → 400."""


class DuplicateProductEventError(Exception):
    """Дубль CREATED для существующей карточки → 400."""


class CardNotFoundError(Exception):
    """Карточка не найдена при EDITED → 400."""


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

    async def _get_reason(self, reason_id: uuid.UUID) -> ProductBlockingReason | None:
        stmt = select(ProductBlockingReason).where(ProductBlockingReason.id == reason_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def decline_product(
        self,
        product_id: uuid.UUID,
        moderator_id: uuid.UUID,
        request: DeclineRequest,
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

        reason = await self._get_reason(request.blocking_reason_id)
        if reason is None:
            raise BlockingReasonNotFoundError("Blocking reason not found")

        hard_block = reason.hard_block
        card.status = (
            ModerationStatus.HARD_BLOCKED if hard_block else ModerationStatus.BLOCKED
        )
        card.date_moderation = datetime.now(UTC)
        card.moderator_comment = request.moderator_comment
        card.blocking_reason_id = reason.id
        card.field_reports = [fr.model_dump(mode="json") for fr in request.field_reports]

        if card.event_idempotency_key is None:
            card.event_idempotency_key = uuid.uuid4()
        await self.session.flush()

        await self.event_publisher.publish_blocked_to_b2b(
            product_id=product_id,
            idempotency_key=card.event_idempotency_key,
            hard_block=hard_block,
            blocking_reason={
                "id": str(reason.id),
                "title": reason.title,
                "comment": request.moderator_comment,
            },
            field_reports=card.field_reports,
        )

        # B2B в этом монорепо — тот же процесс: применяем эффект события.
        product = await self.session.get(Product, product_id)
        if product is not None:
            product.status = (
                ProductStatus.HARD_BLOCKED if hard_block else ProductStatus.BLOCKED
            )
            product.blocking_reason_id = reason.id
            product.blocking_reason_title = reason.title
            product.moderator_comment = request.moderator_comment
            product.field_reports = card.field_reports

        await self.session.flush()
        return card

    async def handle_b2b_product_event(self, event: IncomingProductEvent) -> str:
        """MOD-1: приём B2B-событий CREATED/EDITED/DELETED (Moderation-сторона)."""
        card = await self._get_card(event.product_id)

        if event.event == "CREATED":
            if card is not None:
                if card.status == ModerationStatus.HARD_BLOCKED:
                    return "ignored"
                raise DuplicateProductEventError("Duplicate CREATED for product")
            self.session.add(
                ProductModeration(
                    product_id=event.product_id,
                    status=ModerationStatus.PENDING,
                )
            )
            await self.session.flush()
            return "created"

        if event.event == "EDITED":
            if card is None:
                raise CardNotFoundError("Product not found in moderation queue")
            if card.status == ModerationStatus.HARD_BLOCKED:
                return "ignored"
            card.status = ModerationStatus.PENDING
            card.moderator_id = None
            card.field_reports = []
            await self.session.flush()
            return "updated"

        # DELETED: удаляем карточку (даже HARD_BLOCKED); идемпотентно.
        if card is not None:
            await self.session.delete(card)
            await self.session.flush()
        return "deleted"
