import uuid
from dataclasses import dataclass, field

from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.config import settings
from src.models import SKU, Product, ProductCharacteristic, ProductStatus
from src.schemas.catalog import (
    CatalogProductCard,
    CategoryFilter,
    Facet,
    FacetValue,
)


class B2BUnavailableError(Exception):
    """B2B каталог недоступен (соответствует 502/503 на стороне B2C)."""


@dataclass
class CatalogParams:
    category_id: uuid.UUID | None = None
    price_min: int | None = None
    price_max: int | None = None
    attributes: dict[str, str] = field(default_factory=dict)
    q: str | None = None
    sort: str = "popularity"
    limit: int = 20
    offset: int = 0


class CatalogB2BClient:
    """Доступ к данным B2B для каталога (вариант A — прямая БД).

    Предикат видимости (MODERATED, не удалён, active_quantity>0) выполняется
    здесь — это «сторона B2B». B2C только проксирует параметры.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    def _ensure_available(self) -> None:
        if settings.catalog_b2b_unavailable:
            raise B2BUnavailableError("B2B catalog is unavailable")

    async def search(
        self, params: CatalogParams, cart_sku_ids: set[uuid.UUID] | None = None
    ) -> tuple[list[CatalogProductCard], int]:
        self._ensure_available()
        cart_sku_ids = cart_sku_ids or set()
        filters = self._visibility_filters(params, with_sku=True)

        count_stmt = (
            select(func.count(distinct(Product.id)))
            .select_from(Product)
            .join(SKU, SKU.product_id == Product.id)
            .where(*filters)
        )
        total_count = (await self.session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Product, func.min(SKU.price).label("min_price"))
            .join(SKU, SKU.product_id == Product.id)
            .where(*filters)
            .group_by(Product.id)
            .options(selectinload(Product.images), selectinload(Product.skus))
        )
        stmt = self._apply_sort(stmt, params.sort)
        stmt = stmt.limit(params.limit).offset(params.offset)
        rows = (await self.session.execute(stmt)).all()

        cards = [self._build_card(product, min_price, cart_sku_ids) for product, min_price in rows]
        return cards, total_count

    async def facets(
        self, category_id: uuid.UUID | None, attributes: dict[str, str]
    ) -> list[Facet]:
        self._ensure_available()
        base = self._visibility_filters(
            CatalogParams(category_id=category_id), with_sku=True
        )

        name_stmt = (
            select(ProductCharacteristic.name)
            .select_from(Product)
            .join(ProductCharacteristic, ProductCharacteristic.product_id == Product.id)
            .join(SKU, SKU.product_id == Product.id)
            .where(*base)
            .distinct()
        )
        names = [row[0] for row in (await self.session.execute(name_stmt)).all()]

        facets: list[Facet] = []
        for name in names:
            filters = list(base)
            filters.append(ProductCharacteristic.name == name)
            filters.extend(self._attribute_filters(attributes, skip=name))
            stmt = (
                select(ProductCharacteristic.value, func.count(distinct(Product.id)))
                .select_from(Product)
                .join(ProductCharacteristic, ProductCharacteristic.product_id == Product.id)
                .join(SKU, SKU.product_id == Product.id)
                .where(*filters)
                .group_by(ProductCharacteristic.value)
                .order_by(func.count(distinct(Product.id)).desc())
            )
            rows = (await self.session.execute(stmt)).all()
            facets.append(
                Facet(
                    name=name,
                    values=[FacetValue(value=value, count=count) for value, count in rows],
                )
            )
        return facets

    async def category_filters(
        self, category_id: uuid.UUID | None
    ) -> list[CategoryFilter]:
        self._ensure_available()
        base = self._visibility_filters(
            CatalogParams(category_id=category_id), with_sku=True
        )

        stmt = (
            select(ProductCharacteristic.name, ProductCharacteristic.value)
            .select_from(Product)
            .join(ProductCharacteristic, ProductCharacteristic.product_id == Product.id)
            .join(SKU, SKU.product_id == Product.id)
            .where(*base)
            .distinct()
        )
        rows = (await self.session.execute(stmt)).all()

        grouped: dict[str, set[str]] = {}
        for name, value in rows:
            grouped.setdefault(name, set()).add(value)

        filters = [
            CategoryFilter(slug=name, name=name, type="list", value=sorted(values))
            for name, values in grouped.items()
        ]

        price_stmt = (
            select(func.min(SKU.price), func.max(SKU.price))
            .select_from(Product)
            .join(SKU, SKU.product_id == Product.id)
            .where(*base)
        )
        min_price, max_price = (await self.session.execute(price_stmt)).one()
        if min_price is not None and max_price is not None:
            filters.append(
                CategoryFilter(
                    slug="price", name="Цена", type="range", min=min_price, max=max_price
                )
            )
        return filters

    # --- helpers ---

    @staticmethod
    def _attribute_filters(attributes: dict[str, str], skip: str | None = None) -> list:
        clauses = []
        for name, value in attributes.items():
            if name == skip:
                continue
            clauses.append(
                select(ProductCharacteristic.id)
                .where(
                    ProductCharacteristic.product_id == Product.id,
                    ProductCharacteristic.name == name,
                    ProductCharacteristic.value == value,
                )
                .exists()
            )
        return clauses

    def _visibility_filters(self, params: CatalogParams, with_sku: bool) -> list:
        filters = [
            Product.status == ProductStatus.MODERATED,
            Product.deleted.is_(False),
        ]
        if with_sku:
            filters.append(SKU.active_quantity > 0)
            if params.price_min is not None:
                filters.append(SKU.price >= params.price_min)
            if params.price_max is not None:
                filters.append(SKU.price <= params.price_max)
        if params.category_id is not None:
            filters.append(Product.category_id == params.category_id)
        if params.q:
            filters.append(Product.title.ilike(f"%{params.q}%"))
        filters.extend(self._attribute_filters(params.attributes))
        return filters

    @staticmethod
    def _apply_sort(stmt, sort: str):
        if sort == "price_asc":
            return stmt.order_by(func.min(SKU.price).asc(), Product.id.asc())
        if sort == "price_desc":
            return stmt.order_by(func.min(SKU.price).desc(), Product.id.asc())
        if sort == "discount_desc":
            return stmt.order_by(func.max(SKU.discount).desc(), Product.id.asc())
        # rating, popularity, date_desc, new -> по дате (нет отдельных полей рейтинга/популярности)
        return stmt.order_by(Product.created_at.desc(), Product.id.asc())

    @staticmethod
    def _build_card(
        product: Product, min_price: int | None, cart_sku_ids: set[uuid.UUID]
    ) -> CatalogProductCard:
        in_stock = any(sku.active_quantity > 0 for sku in product.skus)
        images = [image.url for image in product.images]
        price = min_price or 0
        return CatalogProductCard(
            id=product.id,
            title=product.title,
            name=product.title,
            price=price,
            min_price=price,
            image=images[0] if images else None,
            images=images,
            in_stock=in_stock,
            has_stock=in_stock,
            is_in_cart=any(sku.id in cart_sku_ids for sku in product.skus),
        )
