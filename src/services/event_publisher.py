import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
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
            "date": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

        event = OutboxEvent(
            event_type=f"product.{event_type.lower()}",
            aggregate_id=product_id,
            payload=payload,
            service_key=settings.b2b_to_mod_key,
            target_url=f"{settings.moderation_service_url}/api/v1/events/product",
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
            "date": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        }

        event = OutboxEvent(
            event_type=f"product.{event_type.lower()}",
            aggregate_id=product_id,
            payload=payload,
            service_key=settings.b2b_to_b2c_key,
            target_url=f"{settings.b2c_service_url}/api/v1/events/product",
        )
        self.session.add(event)