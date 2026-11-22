import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SKU
from src.models.inventory import ReserveOperation
from src.services.event_publisher import EventPublisher

RESERVE = "reserve"
UNRESERVE = "unreserve"


@dataclass
class FailedItem:
    sku_id: uuid.UUID
    requested: int
    available: int
    reason: str


@dataclass
class ReservedItem:
    sku_id: uuid.UUID
    reserved_quantity: int
    remaining_stock: int


@dataclass
class ReserveResult:
    reserved: bool
    items: list[ReservedItem] = field(default_factory=list)
    failed_items: list[FailedItem] = field(default_factory=list)
    order_id: uuid.UUID | None = None
    reserved_at: datetime | None = None

    def to_json(self) -> dict:
        return {
            "reserved": self.reserved,
            "items": [
                {
                    "sku_id": str(item.sku_id),
                    "reserved_quantity": item.reserved_quantity,
                    "remaining_stock": item.remaining_stock,
                }
                for item in self.items
            ],
            "order_id": str(self.order_id) if self.order_id else None,
            "reserved_at": self.reserved_at.isoformat() if self.reserved_at else None,
        }


def _aggregate(items) -> "dict[uuid.UUID, int]":
    """Суммирует количество по sku_id, сохраняя порядок первого появления."""
    totals: dict[uuid.UUID, int] = {}
    for item in items:
        totals[item.sku_id] = totals.get(item.sku_id, 0) + item.quantity
    return totals


class InventoryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.event_publisher = EventPublisher(session)

    async def reserve(
        self,
        idempotency_key: uuid.UUID,
        items: list,
        order_id: uuid.UUID | None = None,
    ) -> ReserveResult:
        # 1. Идемпотентность: повторный успешный запрос возвращает сохранённый результат.
        cached = await self._get_operation(idempotency_key, RESERVE)
        if cached is not None:
            return self._result_from_cache(cached, order_id)

        quantities = _aggregate(items)

        # 2. Блокируем все задействованные SKU в рамках одной транзакции.
        skus = await self._lock_skus(quantities.keys())

        # 3. Проверка достаточности остатков (all-or-nothing).
        failed_items: list[FailedItem] = []
        for sku in skus:
            requested = quantities[sku.id]
            if sku.active_quantity < requested:
                reason = "OUT_OF_STOCK" if sku.active_quantity == 0 else "INSUFFICIENT_STOCK"
                failed_items.append(
                    FailedItem(
                        sku_id=sku.id,
                        requested=requested,
                        available=sku.active_quantity,
                        reason=reason,
                    )
                )

        if failed_items:
            # Ничего не резервируем: транзакция будет закоммичена без изменений.
            return ReserveResult(reserved=False, failed_items=failed_items)

        # 4. Атомарное списание active -> reserved.
        reserved_at = datetime.now(UTC)
        reserved_items: list[ReservedItem] = []
        out_of_stock: list[SKU] = []
        for sku in skus:
            qty = quantities[sku.id]
            sku.active_quantity -= qty
            sku.reserved_quantity += qty
            reserved_items.append(
                ReservedItem(
                    sku_id=sku.id,
                    reserved_quantity=qty,
                    remaining_stock=sku.active_quantity,
                )
            )
            if sku.active_quantity == 0:
                out_of_stock.append(sku)

        # 5. Сохраняем результат идемпотентной операции.
        result = ReserveResult(
            reserved=True,
            items=reserved_items,
            order_id=order_id,
            reserved_at=reserved_at,
        )
        self.session.add(
            ReserveOperation(
                idempotency_key=idempotency_key,
                operation_type=RESERVE,
                result=result.to_json(),
            )
        )

        # 6. Событие SKU_OUT_OF_STOCK в B2C через outbox (в той же транзакции).
        for sku in out_of_stock:
            await self.event_publisher.publish_sku_out_of_stock(
                sku_id=sku.id,
                product_id=sku.product_id,
            )

        await self.session.flush()
        return result

    async def unreserve(self, order_id: uuid.UUID, items: list) -> datetime:
        cached = await self._get_operation(order_id, UNRESERVE)
        if cached is not None:
            return self._processed_at_from_cache(cached)

        quantities = _aggregate(items)
        skus = await self._lock_skus(quantities.keys())

        processed_at = datetime.now(UTC)
        for sku in skus:
            qty = quantities[sku.id]
            if sku.reserved_quantity < qty:
                raise ValueError("Insufficient reserved quantity")
            sku.reserved_quantity -= qty
            sku.active_quantity += qty

        self.session.add(
            ReserveOperation(
                idempotency_key=order_id,
                operation_type=UNRESERVE,
                result={"processed_at": processed_at.isoformat()},
            )
        )
        await self.session.flush()
        return processed_at

    async def _lock_skus(self, sku_ids) -> list[SKU]:
        ids = list(sku_ids)
        if not ids:
            return []
        stmt = select(SKU).where(SKU.id.in_(ids)).with_for_update()
        result = await self.session.execute(stmt)
        skus = result.scalars().all()
        found = {sku.id for sku in skus}
        missing = set(ids) - found
        if missing:
            raise ValueError("SKU not found")
        return list(skus)

    async def _get_operation(
        self, key: uuid.UUID, operation_type: str
    ) -> ReserveOperation | None:
        stmt = select(ReserveOperation).where(
            ReserveOperation.idempotency_key == key,
            ReserveOperation.operation_type == operation_type,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    def _result_from_cache(
        cached: ReserveOperation, fallback_order_id: uuid.UUID | None
    ) -> ReserveResult:
        data = cached.result
        return ReserveResult(
            reserved=True,
            items=[
                ReservedItem(
                    sku_id=uuid.UUID(item["sku_id"]),
                    reserved_quantity=item["reserved_quantity"],
                    remaining_stock=item["remaining_stock"],
                )
                for item in data.get("items", [])
            ],
            order_id=uuid.UUID(data["order_id"]) if data.get("order_id") else fallback_order_id,
            reserved_at=datetime.fromisoformat(data["reserved_at"]),
        )

    @staticmethod
    def _processed_at_from_cache(cached: ReserveOperation) -> datetime:
        return datetime.fromisoformat(cached.result["processed_at"])
