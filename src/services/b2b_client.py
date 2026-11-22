import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SKU, Product


@dataclass
class SkuSnapshot:
    sku_id: uuid.UUID
    product_id: uuid.UUID
    product_status: str
    product_deleted: bool
    name: str
    product_title: str
    price: int
    image: str
    active_quantity: int


class B2BClient:
    """Доступ к данным B2B для обогащения корзины.

    В варианте A (единый репозиторий) читает БД напрямую, но НЕ применяет
    фильтр видимости витрины: корзине нужен статус и остаток любого товара,
    включая заблокированные/удалённые/out-of-stock, чтобы вычислить
    unavailable_reason. В B2C-режиме этот класс заменяется на HTTP-клиент
    с X-Service-Key.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_skus_batch(
        self, sku_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, SkuSnapshot]:
        if not sku_ids:
            return {}
        stmt = (
            select(SKU, Product)
            .join(Product, SKU.product_id == Product.id)
            .where(SKU.id.in_(sku_ids))
        )
        result = await self.session.execute(stmt)
        snapshots: dict[uuid.UUID, SkuSnapshot] = {}
        for sku, product in result.all():
            snapshots[sku.id] = SkuSnapshot(
                sku_id=sku.id,
                product_id=product.id,
                product_status=product.status.value,
                product_deleted=product.deleted,
                name=sku.name,
                product_title=product.title,
                price=sku.price,
                image=sku.image,
                active_quantity=sku.active_quantity,
            )
        return snapshots
