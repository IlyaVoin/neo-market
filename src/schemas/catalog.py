import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

CatalogSort = Literal[
    "rating",
    "popularity",
    "price_asc",
    "price_desc",
    "date_desc",
    "discount_desc",
]


class CatalogProductCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    title: str
    name: str
    price: int
    min_price: int
    image: str | None = None
    images: list[str] = []
    in_stock: bool
    has_stock: bool
    is_in_cart: bool = False


class CatalogProductListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CatalogProductCard]
    total_count: int
    limit: int
    offset: int


class FacetValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    count: int


class Facet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    values: list[FacetValue]


class FacetsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID | None = None
    facets: list[Facet]


class CategoryFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str
    name: str
    type: Literal["list", "range", "switch"]
    value: list[str] | None = None
    min: Annotated[int, Field(ge=0)] | None = None
    max: Annotated[int, Field(ge=0)] | None = None


class CategoryFiltersResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CategoryFilter]
