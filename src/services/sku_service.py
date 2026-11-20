import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import Product, ProductStatus, SKU, SKUCharacteristic
from src.schemas.sku import SKUCharacteristicCreate, SKUCreate, SKUCharacteristicResponse, SKUResponse
from src.services.event_publisher import EventPublisher

if TYPE_CHECKING:
    pass


class SKUService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.event_publisher = EventPublisher(session)

    async def create_sku(self, seller_id: uuid.UUID, sku_data: SKUCreate) -> SKUResponse:
        product = await self._get_product_and_validate(sku_data.product_id, seller_id)

        if product.status == ProductStatus.HARD_BLOCKED:
            raise ValueError("Cannot add SKU to hard-blocked product")

        existing_skus_count = await self._count_product_skus(product.id)
        is_first_sku = existing_skus_count == 0

        sku = SKU(
            product_id=product.id,
            name=sku_data.name,
            price=sku_data.price,
            cost_price=sku_data.cost_price,
            discount=sku_data.discount,
            image=sku_data.image,
            active_quantity=0,
            reserved_quantity=0,
        )
        self.session.add(sku)
        await self.session.flush()

        for char_data in sku_data.characteristics:
            characteristic = SKUCharacteristic(
                sku_id=sku.id,
                name=char_data.name,
                value=char_data.value,
            )
            self.session.add(characteristic)

        await self.session.flush()

        if is_first_sku and product.status == ProductStatus.CREATED:
            product.status = ProductStatus.ON_MODERATION
            await self.event_publisher.publish_moderation_event(
                event_type="CREATED",
                product_id=product.id,
                seller_id=product.seller_id,
            )
        elif product.status in (ProductStatus.MODERATED, ProductStatus.BLOCKED):
            product.status = ProductStatus.ON_MODERATION
            await self.event_publisher.publish_moderation_event(
                event_type="EDITED",
                product_id=product.id,
                seller_id=product.seller_id,
            )

        await self.session.flush()

        stmt = (
            select(SKU)
            .options(selectinload(SKU.characteristics))
            .where(SKU.id == sku.id)
        )
        result = await self.session.execute(stmt)
        sku_with_relations = result.scalar_one()

        return self._to_response(sku_with_relations)

    async def _get_product_and_validate(self, product_id: uuid.UUID, seller_id: uuid.UUID) -> Product:
        stmt = select(Product).where(Product.id == product_id)
        result = await self.session.execute(stmt)
        product = result.scalar_one_or_none()

        if product is None:
            raise ValueError("Product not found")

        if product.seller_id != seller_id:
            raise ValueError("Product not found")

        return product

    async def _count_product_skus(self, product_id: uuid.UUID) -> int:
        stmt = select(func.count(SKU.id)).where(SKU.product_id == product_id)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    def _to_response(self, sku: SKU) -> SKUResponse:
        return SKUResponse(
            id=sku.id,
            product_id=sku.product_id,
            name=sku.name,
            price=sku.price,
            cost_price=sku.cost_price,
            discount=sku.discount,
            image=sku.image,
            active_quantity=sku.active_quantity,
            reserved_quantity=sku.reserved_quantity,
            characteristics=[
                SKUCharacteristicResponse(
                    id=char.id,
                    name=char.name,
                    value=char.value,
                )
                for char in sku.characteristics
            ],
            created_at=sku.created_at,
            updated_at=sku.updated_at,
        )