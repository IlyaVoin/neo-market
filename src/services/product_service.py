import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import Category, Product, ProductCharacteristic, ProductImage, SKU
from src.schemas.product import (
    BlockingReason,
    FieldReport,
    ProductCharacteristicResponse,
    ProductCreate,
    ProductDetailResponse,
    ProductImageResponse,
    ProductResponse,
    SKUDetailResponse,
    SKUCharacteristicResponse,
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

    async def get_product(
        self,
        product_id: uuid.UUID,
        seller_id: uuid.UUID | None = None,
        service_key: str | None = None,
    ) -> ProductDetailResponse:
        stmt = (
            select(Product)
            .options(
                selectinload(Product.images),
                selectinload(Product.characteristics),
                selectinload(Product.skus).selectinload(SKU.characteristics),
            )
            .where(Product.id == product_id)
        )
        result = await self.session.execute(stmt)
        product = result.scalar_one_or_none()

        if product is None:
            raise ValueError("Product not found")

        if service_key is None and seller_id is not None and product.seller_id != seller_id:
            raise ValueError("Product not found")

        return self._to_detail_response(product, service_key=service_key)

    def _to_detail_response(
        self, product: Product, service_key: str | None = None
    ) -> ProductDetailResponse:
        from src.models.enums import ProductStatus

        blocked = product.status in (ProductStatus.BLOCKED, ProductStatus.HARD_BLOCKED)

        if product.blocking_reason_id is not None:
            blocking_reason = BlockingReason(
                id=product.blocking_reason_id,
                title=product.moderator_comment or product.status.value,
                comment=product.moderator_comment,
            )
        else:
            blocking_reason = None

        field_reports: list[FieldReport] = []

        skus: list[SKUDetailResponse] = []
        for sku in product.skus:
            if service_key is not None:
                cost_price = None
                reserved_quantity = None
            else:
                cost_price = sku.cost_price
                reserved_quantity = sku.reserved_quantity
            skus.append(
                SKUDetailResponse(
                    id=sku.id,
                    product_id=sku.product_id,
                    name=sku.name,
                    price=sku.price,
                    cost_price=cost_price,
                    discount=sku.discount,
                    image=sku.image,
                    active_quantity=sku.active_quantity,
                    reserved_quantity=reserved_quantity,
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
            )

        return ProductDetailResponse(
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
            blocked=blocked,
            blocking_reason=blocking_reason,
            field_reports=field_reports,
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
            skus=skus,
            created_at=product.created_at,
            updated_at=product.updated_at,
        )

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
