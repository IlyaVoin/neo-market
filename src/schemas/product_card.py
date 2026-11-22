import uuid

from pydantic import BaseModel, ConfigDict

from src.schemas.product import (
    ProductCharacteristicResponse,
    ProductImageResponse,
    SKUCharacteristicResponse,
)


class ProductCardSku(BaseModel):
    """SKU для B2C-карточки. Явный allowlist: нет cost_price и reserved_quantity."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    name: str
    price: int
    discount: int
    image: str
    active_quantity: int
    in_stock: bool
    characteristics: list[SKUCharacteristicResponse]


class ProductCardResponse(BaseModel):
    """B2C-карточка товара. Явный allowlist: нет seller_id, blocking_reason, moderator_comment."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    slug: str | None
    title: str
    description: str
    status: str
    images: list[ProductImageResponse]
    characteristics: list[ProductCharacteristicResponse]
    skus: list[ProductCardSku]
