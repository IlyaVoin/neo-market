import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import CartItem
from src.schemas.catalog import (
    CatalogProductListResponse,
    CategoryFiltersResponse,
    FacetsResponse,
)
from src.services.cart_service import CartIdentity
from src.services.catalog_b2b_client import CatalogB2BClient, CatalogParams

ALLOWED_SORTS = [
    "rating",
    "popularity",
    "price_asc",
    "price_desc",
    "date_desc",
    "discount_desc",
]
_SORT_ALIASES = {"new": "date_desc"}
SORT_ERROR_MESSAGE = "Invalid sort parameter. Allowed: " + ", ".join(ALLOWED_SORTS)


class InvalidSortError(ValueError):
    def __init__(self) -> None:
        super().__init__(SORT_ERROR_MESSAGE)


class CatalogService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.b2b = CatalogB2BClient(session)

    async def search(
        self,
        params: CatalogParams,
        identity: CartIdentity | None,
    ) -> CatalogProductListResponse:
        params.sort = self._normalize_sort(params.sort)
        cart_sku_ids = await self._cart_sku_ids(identity)
        cards, total_count = await self.b2b.search(params, cart_sku_ids)
        return CatalogProductListResponse(
            items=cards, total_count=total_count, limit=params.limit, offset=params.offset
        )

    async def facets(
        self, category_id: uuid.UUID | None, attributes: dict[str, str]
    ) -> FacetsResponse:
        facets = await self.b2b.facets(category_id, attributes)
        return FacetsResponse(category_id=category_id, facets=facets)

    async def category_filters(
        self, category_id: uuid.UUID | None
    ) -> CategoryFiltersResponse:
        items = await self.b2b.category_filters(category_id)
        return CategoryFiltersResponse(items=items)

    @staticmethod
    def _normalize_sort(sort: str) -> str:
        if sort in _SORT_ALIASES:
            return _SORT_ALIASES[sort]
        if sort not in ALLOWED_SORTS:
            raise InvalidSortError
        return sort

    async def _cart_sku_ids(self, identity: CartIdentity | None) -> set[uuid.UUID]:
        if identity is None:
            return set()
        stmt = select(CartItem.sku_id)
        if identity.user_id is not None:
            stmt = stmt.where(CartItem.user_id == identity.user_id)
        elif identity.session_id is not None:
            stmt = stmt.where(CartItem.session_id == identity.session_id)
        else:
            return set()
        return set((await self.session.execute(stmt)).scalars().all())
