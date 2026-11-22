import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models import OutboxEvent


class EventPublisher:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def publish_moderation_event(
        self,
        event_type: str,
        product_id: uuid.UUID,
        seller_id: uuid.UUID,
        idempotency_key: uuid.UUID | None = None,
    ) -> None:
        if idempotency_key is None:
            idempotency_key = uuid.uuid4()

        payload = {
            "idempotency_key": str(idempotency_key),
            "product_id": str(product_id),
            "seller_id": str(seller_id),
            "event": event_type,
            "date": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

        event = OutboxEvent(
            event_type=f"product.{event_type.lower()}",
            aggregate_id=product_id,
            payload=payload,
            service_key=settings.b2b_to_mod_key,
            target_url=f"{settings.moderation_service_url}/api/v1/events/product",
        )
        self.session.add(event)

    async def publish_moderated_to_b2b(
        self,
        product_id: uuid.UUID,
        idempotency_key: uuid.UUID,
    ) -> None:
        """Событие MODERATED от Moderation в B2B (approve)."""
        payload = {
            "idempotency_key": str(idempotency_key),
            "product_id": str(product_id),
            "status": "MODERATED",
            "date": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

        event = OutboxEvent(
            event_type="moderation.moderated",
            aggregate_id=product_id,
            payload=payload,
            service_key=settings.mod_to_b2b_key,
            target_url=f"{settings.b2b_service_url}/api/v1/events/moderation",
        )
        self.session.add(event)

    async def publish_blocked_to_b2b(
        self,
        product_id: uuid.UUID,
        idempotency_key: uuid.UUID,
        hard_block: bool,
        blocking_reason: dict,
        field_reports: list[dict] | None = None,
    ) -> None:
        """Событие BLOCKED от Moderation в B2B (MOD-4/MOD-5).

        hard_block=true → B2B переводит товар в HARD_BLOCKED (терминал) + каскад.
        Идемпотентность: стабильный idempotency_key; приём дедуплицирует.
        """
        payload = {
            "idempotency_key": str(idempotency_key),
            "product_id": str(product_id),
            "status": "BLOCKED",
            "hard_block": hard_block,
            "blocking_reason": blocking_reason,
            "field_reports": field_reports or [],
            "date": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

        event = OutboxEvent(
            event_type="moderation.blocked",
            aggregate_id=product_id,
            payload=payload,
            service_key=settings.mod_to_b2b_key,
            target_url=f"{settings.b2b_service_url}/api/v1/events/moderation",
        )
        self.session.add(event)

    async def publish_b2c_event(
        self,
        event_type: str,
        product_id: uuid.UUID,
        sku_ids: list[uuid.UUID],
        idempotency_key: uuid.UUID | None = None,
    ) -> None:
        if idempotency_key is None:
            idempotency_key = uuid.uuid4()

        payload = {
            "idempotency_key": str(idempotency_key),
            "event": event_type,
            "product_id": str(product_id),
            "sku_ids": [str(sku_id) for sku_id in sku_ids],
            "date": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

        event = OutboxEvent(
            event_type=f"product.{event_type.lower()}",
            aggregate_id=product_id,
            payload=payload,
            service_key=settings.b2b_to_b2c_key,
            target_url=f"{settings.b2c_service_url}/api/v1/events/product",
        )
        self.session.add(event)

    async def publish_sku_out_of_stock(
        self,
        sku_id: uuid.UUID,
        product_id: uuid.UUID,
        idempotency_key: uuid.UUID | None = None,
    ) -> None:
        if idempotency_key is None:
            idempotency_key = uuid.uuid4()

        payload = {
            "idempotency_key": str(idempotency_key),
            "event": "SKU_OUT_OF_STOCK",
            "sku_id": str(sku_id),
            "product_id": str(product_id),
            "date": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

        event = OutboxEvent(
            event_type="sku.out_of_stock",
            aggregate_id=sku_id,
            payload=payload,
            service_key=settings.b2b_to_b2c_key,
            target_url=f"{settings.b2c_service_url}/api/v1/events/inventory",
        )
        self.session.add(event)
