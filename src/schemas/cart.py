import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

UnavailableReason = Literal[
    "OUT_OF_STOCK",
    "PRODUCT_BLOCKED",
    "PRODUCT_DELETED",
    "PRODUCT_DELISTED",
    "ON_MODERATION",
]


class CartItemAddRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    quantity: Annotated[int, Field(ge=1)]


class CartItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    quantity: Annotated[int, Field(ge=1)]


class CartItemView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: uuid.UUID
    sku_id: uuid.UUID
    product_id: uuid.UUID | None = None
    title: str | None = None
    price: int | None = None
    image: str | None = None
    quantity: int
    available: bool
    unavailable_reason: UnavailableReason | None = None
    available_stock: int | None = None
    line_total: int = 0


class CartSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_amount: int
    total_items: int
    unavailable_count: int
    checkout_ready: bool


class CheckoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    quantity: int


class CheckoutPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CheckoutItem]


class CartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CartItemView]
    summary: CartSummary
    checkout_payload: CheckoutPayload


class CartValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issue_type: str
    severity: Literal["critical", "warning"]
    message: str


class CartValidationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_valid: bool
    can_checkout: bool
    issues: list[CartValidationIssue]
