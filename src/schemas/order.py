import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class OrderItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    quantity: int


class OrderSnapshotItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    quantity: Annotated[int, Field(ge=1)]
    unit_price: int | None = None


class OrderCreateRequest(BaseModel):
    """Запрос на checkout. Принимает канон- и OpenAPI-поля."""

    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID | None = None
    items: list[OrderItemRequest]
    delivery_address: str | None = None
    comment: str | None = Field(default=None, max_length=1000)
    address_id: uuid.UUID | None = None
    payment_method_id: uuid.UUID | None = None
    items_snapshot: list[OrderSnapshotItem] | None = None


class FailedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    requested: int | None = None
    available: int | None = None
    reason: str


class OrderItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    sku_id: uuid.UUID
    product_id: uuid.UUID
    product_title: str
    sku_name: str
    name: str
    sku_code: str | None = None
    image_url: str | None = None
    quantity: int
    unit_price: int
    line_total: int


class OrderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    number: str | None = None
    buyer_id: uuid.UUID
    status: Literal[
        "CREATED",
        "PAID",
        "ASSEMBLING",
        "DELIVERING",
        "DELIVERED",
        "CANCELLED",
        "CANCEL_PENDING",
    ]
    items: list[OrderItemResponse]
    subtotal: int
    delivery_cost: int = 0
    total: int
    total_amount: int
    address: str | None = None
    delivery_address: str | None = None
    comment: str | None = None
    created_at: datetime
    updated_at: datetime
