import uuid

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models import SKU, Category, Product, ProductCharacteristic, ProductImage, ProductStatus
from src.schemas.product import (
    BlockingReason,
    FieldReport,
    ProductCharacteristicResponse,
    ProductCreate,
    ProductDetailResponse,
    ProductImageResponse,
    ProductPublicPaginatedResponse,
    ProductPublicShortResponse,
    ProductResponse,
    SKUCharacteristicResponse,
    SKUDetailResponse,
    SKUPublicResponse,
)
from src.schemas.product_card import ProductCardResponse, ProductCardSku
from src.services.event_publisher import EventPublisher


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

    async def get_product_card(
        self,
        product_id: uuid.UUID,
    ) -> ProductCardResponse:
        """B2C-карточка товара. Только MODERATED и не удалённый; без cost_price/reserved_quantity."""
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

        if product is None or product.status != ProductStatus.MODERATED or product.deleted:
            raise ValueError("Product not found")

        return ProductCardResponse(
            id=product.id,
            slug=product.slug,
            title=product.title,
            description=product.description,
            status=product.status.value,
            images=[ProductImageResponse.model_validate(image) for image in product.images],
            characteristics=[
                ProductCharacteristicResponse.model_validate(char)
                for char in product.characteristics
            ],
            skus=[
                ProductCardSku(
                    id=sku.id,
                    name=sku.name,
                    price=sku.price,
                    discount=sku.discount,
                    image=sku.image,
                    active_quantity=sku.active_quantity,
                    in_stock=sku.active_quantity > 0,
                    characteristics=[
                        SKUCharacteristicResponse.model_validate(char)
                        for char in sku.characteristics
                    ],
                )
                for sku in product.skus
            ],
        )

    async def update_product(
        self,
        product_id: uuid.UUID,
        seller_id: uuid.UUID,
        product_data,
    ) -> ProductDetailResponse:
        stmt = select(Product).where(Product.id == product_id).with_for_update()
        result = await self.session.execute(stmt)
        product = result.scalar_one_or_none()

        if product is None or product.seller_id != seller_id:
            raise ValueError("Product not found")

        if product.status == ProductStatus.HARD_BLOCKED:
            raise ValueError("Cannot edit hard-blocked product")

        if product_data.title is not None:
            product.title = product_data.title
        if product_data.description is not None:
            product.description = product_data.description
        if product_data.category_id is not None:
            await self._validate_category(product_data.category_id)
            product.category_id = product_data.category_id

        if product.status in (ProductStatus.MODERATED, ProductStatus.BLOCKED):
            product.status = ProductStatus.ON_MODERATION
            publisher = EventPublisher(self.session)
            await publisher.publish_moderation_event(
                event_type="EDITED",
                product_id=product.id,
                seller_id=product.seller_id,
            )

        await self.session.flush()

        stmt = (
            select(Product)
            .options(
                selectinload(Product.images),
                selectinload(Product.characteristics),
                selectinload(Product.skus).selectinload(SKU.characteristics),
            )
            .where(Product.id == product.id)
        )
        result = await self.session.execute(stmt)
        product = result.scalar_one()
        return self._to_detail_response(product)

    async def delete_product(self, product_id: uuid.UUID, seller_id: uuid.UUID) -> None:
        stmt = select(Product).where(Product.id == product_id).with_for_update()
        result = await self.session.execute(stmt)
        product = result.scalar_one_or_none()

        if product is None or product.seller_id != seller_id:
            raise ValueError("Product not found")

        if product.status == ProductStatus.HARD_BLOCKED:
            raise ValueError("Cannot delete hard-blocked product")

        if product.deleted:
            raise ValueError("Product already deleted")

        product.deleted = True
        await self.session.flush()

    async def list_products(
        self,
        service_key: str,
        ids: list[uuid.UUID] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> ProductPublicPaginatedResponse:
        """B2C catalog mode: returns MODERATED, non-deleted products with active_quantity > 0."""
        from src.models.enums import ProductStatus

        stmt = (
            select(Product)
            .join(Product.skus)
            .options(
                selectinload(Product.images),
                selectinload(Product.characteristics),
                selectinload(Product.skus).selectinload(SKU.characteristics),
            )
            .where(
                Product.status == ProductStatus.MODERATED,
                Product.deleted.is_(False),
                SKU.active_quantity > 0,
            )
            .order_by(Product.created_at.desc())
            .distinct()
        )

        if ids:
            stmt = stmt.where(Product.id.in_(ids))

        count_stmt = (
            select(func.count(distinct(Product.id)))
            .join(Product.skus)
            .where(
                Product.status == ProductStatus.MODERATED,
                Product.deleted.is_(False),
                SKU.active_quantity > 0,
            )
        )
        if ids:
            count_stmt = count_stmt.where(Product.id.in_(ids))
        total_result = await self.session.execute(count_stmt)
        total_count = total_result.scalar_one()

        stmt = stmt.limit(limit).offset(offset)
        result = await self.session.execute(stmt)
        products = result.scalars().all()

        items: list[ProductPublicShortResponse] = []
        for product in products:
            public_skus = [
                self._to_public_sku(sku) for sku in product.skus if sku.active_quantity > 0
            ]
            if not public_skus:
                continue
            items.append(
                ProductPublicShortResponse(
                    id=product.id,
                    category_id=product.category_id,
                    title=product.title,
                    slug=product.slug,
                    description=product.description,
                    status=product.status.value,
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
                    skus=public_skus,
                    created_at=product.created_at,
                    updated_at=product.updated_at,
                )
            )

        return ProductPublicPaginatedResponse(
            items=items,
            total_count=total_count,
            limit=limit,
            offset=offset,
        )

    def _to_public_sku(self, sku: SKU) -> SKUPublicResponse:
        return SKUPublicResponse(
            id=sku.id,
            product_id=sku.product_id,
            name=sku.name,
            price=sku.price,
            discount=sku.discount,
            image=sku.image,
            active_quantity=sku.active_quantity,
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

    def _to_detail_response(
        self, product: Product, service_key: str | None = None
    ) -> ProductDetailResponse:
        from src.models.enums import ProductStatus

        blocked = product.status in (ProductStatus.BLOCKED, ProductStatus.HARD_BLOCKED)

        if blocked:
            blocking_reason = BlockingReason(
                id=product.blocking_reason_id,
                title=product.blocking_reason_title
                or product.moderator_comment
                or product.status.value,
                comment=product.moderator_comment,
            )
        else:
            blocking_reason = None

        field_reports = [
            FieldReport(
                field_name=report.get("field_name", ""),
                sku_id=uuid.UUID(report["sku_id"]) if report.get("sku_id") else None,
                comment=report.get("comment"),
            )
            for report in (product.field_reports or [])
        ]

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
