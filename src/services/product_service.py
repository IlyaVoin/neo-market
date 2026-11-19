import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import Category, Product, ProductCharacteristic, ProductImage
from src.schemas.product import (
    ProductCharacteristicResponse,
    ProductCreate,
    ProductImageResponse,
    ProductResponse,
)


class ProductService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_product(
        self,
        seller_id: uuid.UUID,
        product_data: ProductCreate,
    ) -> ProductResponse:
        await self._validate_category(product_data.category_id)

        product = Product(
            seller_id=seller_id,
            category_id=product_data.category_id,
            title=product_data.title,
            description=product_data.description,
            slug=self._generate_slug(product_data.title),
        )

        self.session.add(product)
        await self.session.flush()

        for img_data in product_data.images:
            image = ProductImage(
                product_id=product.id,
                url=img_data.url,
                ordering=img_data.ordering,
            )
            self.session.add(image)

        for char_data in product_data.characteristics:
            characteristic = ProductCharacteristic(
                product_id=product.id,
                name=char_data.name,
                value=char_data.value,
            )
            self.session.add(characteristic)

        await self.session.flush()

        stmt = (
            select(Product)
            .options(
                selectinload(Product.images),
                selectinload(Product.characteristics),
            )
            .where(Product.id == product.id)
        )
        result = await self.session.execute(stmt)
        product_with_relations = result.scalar_one()

        return self._to_response(product_with_relations)

    async def _validate_category(self, category_id: uuid.UUID) -> None:
        stmt = select(Category).where(Category.id == category_id)
        result = await self.session.execute(stmt)
        category = result.scalar_one_or_none()
        if category is None:
            raise ValueError("Category not found")

    def _generate_slug(self, title: str) -> str:
        import re
        slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")
        return slug[:255] if slug else "product"

    def _to_response(self, product: Product) -> ProductResponse:
        return ProductResponse(
            id=product.id,
            seller_id=product.seller_id,
            category_id=product.category_id,
            title=product.title,
            slug=product.slug,
            description=product.description,
            status=product.status.value,
            deleted=product.deleted,
            blocking_reason_id=product.blocking_reason_id,
            moderator_comment=product.moderator_comment,
            images=[
                ProductImageResponse(
                    id=img.id,
                    url=img.url,
                    ordering=img.ordering,
                )
                for img in product.images
            ],
            characteristics=[
                ProductCharacteristicResponse(
                    id=char.id,
                    name=char.name,
                    value=char.value,
                )
                for char in product.characteristics
            ],
            skus=[],
            created_at=product.created_at,
            updated_at=product.updated_at,
        )
