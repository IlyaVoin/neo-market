import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SKU, Product, ProductStatus
from src.models.moderation import ProcessedEvent
from src.services.event_publisher import EventPublisher


@dataclass
class ModerationDecision:
    idempotency_key: uuid.UUID
    product_id: uuid.UUID
    event_type: str  # "MODERATED" | "BLOCKED"
    hard_block: bool = False
    blocking_reason_id: uuid.UUID | None = None
    blocking_reason_title: str | None = None
    blocking_reason_comment: str | None = None
    field_reports: list[dict] = field(default_factory=list)


@dataclass
class ApplyResult:
    duplicate: bool
    product_id: uuid.UUID
    status: str


class ModerationService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.event_publisher = EventPublisher(session)

    async def apply_event(self, decision: ModerationDecision) -> ApplyResult:
        # 1. Идемпотентность: уже обработанное событие не даёт побочных эффектов.
        cached = await self._get_processed(decision.idempotency_key)
        if cached is not None:
            return ApplyResult(
                duplicate=True,
                product_id=decision.product_id,
                status=cached.result.get("status", ""),
            )

        # 2. Блокируем продукт на время применения.
        product = await self._lock_product(decision.product_id)

        # 3. Применяем вердикт + фиксируем факт обработки в одной транзакции.
        try:
            processed = ProcessedEvent(
                idempotency_key=decision.idempotency_key,
                event_type=decision.event_type,
                result={},
            )
            self.session.add(processed)
            await self.session.flush()
        except IntegrityError:
            # Параллельный дубль: кто-то уже вставил ключ — откатываем свои изменения.
            await self.session.rollback()
            return ApplyResult(
                duplicate=True,
                product_id=decision.product_id,
                status=self._resulting_status(decision),
            )

        if decision.event_type == "MODERATED":
            self._apply_moderated(product)
        else:
            await self._apply_blocked(product, decision)

        processed.result = {
            "product_id": str(product.id),
            "event_type": decision.event_type,
            "status": product.status.value,
        }
        await self.session.flush()

        return ApplyResult(
            duplicate=False,
            product_id=product.id,
            status=product.status.value,
        )

    def _apply_moderated(self, product: Product) -> None:
        product.status = ProductStatus.MODERATED
        product.blocking_reason_id = None
        product.blocking_reason_title = None
        product.moderator_comment = None
        product.field_reports = []

    async def _apply_blocked(self, product: Product, decision: ModerationDecision) -> None:
        product.status = (
            ProductStatus.HARD_BLOCKED if decision.hard_block else ProductStatus.BLOCKED
        )
        product.blocking_reason_id = decision.blocking_reason_id
        product.blocking_reason_title = decision.blocking_reason_title
        product.moderator_comment = decision.blocking_reason_comment
        product.field_reports = [
            {
                "field_name": fr["field_name"],
                "sku_id": str(fr["sku_id"]) if fr.get("sku_id") else None,
                "comment": fr.get("comment"),
            }
            for fr in decision.field_reports
        ]

        sku_ids = await self._product_sku_ids(product.id)
        await self.event_publisher.publish_b2c_event(
            event_type="PRODUCT_BLOCKED",
            product_id=product.id,
            sku_ids=sku_ids,
        )

    @staticmethod
    def _resulting_status(decision: ModerationDecision) -> str:
        if decision.event_type == "MODERATED":
            return ProductStatus.MODERATED.value
        return (
            ProductStatus.HARD_BLOCKED.value
            if decision.hard_block
            else ProductStatus.BLOCKED.value
        )

    async def _lock_product(self, product_id: uuid.UUID) -> Product:
        stmt = select(Product).where(Product.id == product_id).with_for_update()
        result = await self.session.execute(stmt)
        product = result.scalar_one_or_none()
        if product is None:
            raise ValueError("Product not found")
        return product

    async def _product_sku_ids(self, product_id: uuid.UUID) -> list[uuid.UUID]:
        stmt = select(SKU.id).where(SKU.product_id == product_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def _get_processed(self, key: uuid.UUID) -> ProcessedEvent | None:
        stmt = select(ProcessedEvent).where(ProcessedEvent.idempotency_key == key)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
