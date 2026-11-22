import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.models import Order
from src.models.enums import OrderStatus
from src.models.order import OrderItem
from src.schemas.order import FailedItem, OrderCreateRequest
from src.services.b2b_client import B2BClient
from src.services.inventory_service import InventoryService


class EmptyCartError(Exception):
    """items пуст → 400 INVALID_REQUEST."""


class InvalidQuantityError(Exception):
    """quantity < 1 → 422 INVALID_QUANTITY."""


class ReserveFailedError(Exception):
    """Не удалось зарезервировать (all-or-nothing) → 409 RESERVE_FAILED."""

    def __init__(self, failed_items: list[FailedItem]):
        self.failed_items = failed_items
        super().__init__("reserve failed")


class BodyMismatchError(Exception):
    """Тот же idempotency_key, но другое тело → 409."""


class B2BUnavailableError(Exception):
    """B2B недоступен → 503 B2B_UNAVAILABLE."""


class OrderService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.b2b = B2BClient(session)
        self.inventory = InventoryService(session)

    @staticmethod
    def _fingerprint(payload: OrderCreateRequest) -> str:
        canonical = json.dumps(
            {
                "items": [
                    {"sku_id": str(item.sku_id), "quantity": item.quantity}
                    for item in payload.items
                ],
                "delivery_address": payload.delivery_address,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def _get_by_key(self, idempotency_key: uuid.UUID) -> Order | None:
        stmt = (
            select(Order)
            .where(Order.idempotency_key == idempotency_key)
            .options(selectinload(Order.items))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _item_failure(item, snapshot) -> FailedItem | None:
        if snapshot is None:
            return FailedItem(sku_id=item.sku_id, reason="SKU_NOT_FOUND")
        if snapshot.product_deleted:
            return FailedItem(sku_id=item.sku_id, reason="PRODUCT_DELETED")
        if snapshot.product_status != "MODERATED":
            return FailedItem(sku_id=item.sku_id, reason="PRODUCT_BLOCKED")
        if snapshot.active_quantity < item.quantity:
            return FailedItem(
                sku_id=item.sku_id,
                requested=item.quantity,
                available=snapshot.active_quantity,
                reason="OUT_OF_STOCK"
                if snapshot.active_quantity == 0
                else "INSUFFICIENT_STOCK",
            )
        return None

    async def create_order(
        self,
        buyer_id: uuid.UUID,
        payload: OrderCreateRequest,
        idempotency_key: uuid.UUID,
    ) -> tuple[Order, bool]:
        if settings.b2b_unavailable:
            raise B2BUnavailableError("B2B is unavailable")

        fingerprint = self._fingerprint(payload)

        # 0. Idempotency check.
        existing = await self._get_by_key(idempotency_key)
        if existing is not None:
            if existing.idempotency_fingerprint != fingerprint:
                raise BodyMismatchError("idempotency_key reused with different body")
            return existing, False

        # 1. Валидация items.
        self._validate_items(payload)

        # 2. Проверка наличия/статуса + снапшот цен.
        sku_ids = [item.sku_id for item in payload.items]
        snapshots = await self.b2b.get_skus_batch(sku_ids)

        failures = [
            f
            for item in payload.items
            if (f := self._item_failure(item, snapshots.get(item.sku_id))) is not None
        ]
        if failures:
            raise ReserveFailedError(failures)

        # 3. Резервирование (all-or-nothing, идемпотентно по ключу).
        reserve_result = await self.inventory.reserve(idempotency_key, payload.items)
        if not reserve_result.reserved:
            raise ReserveFailedError(
                [
                    FailedItem(
                        sku_id=f.sku_id,
                        requested=f.requested,
                        available=f.available,
                        reason=f.reason,
                    )
                    for f in reserve_result.failed_items
                ]
            )

        # 4. Создание заказа + фиксация цен (в общей транзакции).
        return await self._persist_order(
            buyer_id, payload, idempotency_key, fingerprint, snapshots
        )

    @staticmethod
    def _validate_items(payload: OrderCreateRequest) -> None:
        if not payload.items:
            raise EmptyCartError("items is empty")
        for item in payload.items:
            if item.quantity < 1:
                raise InvalidQuantityError("quantity must be >= 1")

    async def _persist_order(
        self,
        buyer_id: uuid.UUID,
        payload: OrderCreateRequest,
        idempotency_key: uuid.UUID,
        fingerprint: str,
        snapshots,
    ) -> tuple[Order, bool]:
        order = Order(
            buyer_id=buyer_id,
            status=OrderStatus.PAID,
            idempotency_key=idempotency_key,
            idempotency_fingerprint=fingerprint,
            delivery_address=payload.delivery_address,
            total_amount=0,
        )
        self.session.add(order)
        await self.session.flush()

        total = 0
        for item in payload.items:
            snap = snapshots[item.sku_id]
            line_total = snap.price * item.quantity
            total += line_total
            self.session.add(
                OrderItem(
                    order_id=order.id,
                    sku_id=snap.sku_id,
                    product_id=snap.product_id,
                    product_title=snap.product_title,
                    sku_name=snap.name,
                    image_url=snap.image,
                    quantity=item.quantity,
                    unit_price=snap.price,
                    line_total=line_total,
                )
            )

        order.total_amount = total

        try:
            await self.session.flush()
        except IntegrityError:
            # Гонка: другой запрос с тем же idempotency_key уже создал заказ.
            await self.session.rollback()
            existing = await self._get_by_key(idempotency_key)
            if existing is not None:
                return existing, False
            raise

        return order, True
