import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class InventoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    quantity: Annotated[int, Field(gt=0, description="Quantity to reserve/unreserve")]


# --- POST /api/v1/reserve (canon b2b-flows.md#reserve-sku) ---


class ReserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    items: Annotated[list[InventoryItem], Field(min_length=1)]


class ReserveItemResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    reserved_quantity: int
    remaining_stock: int


class ReserveSuccessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reserved: Literal[True] = True
    items: list[ReserveItemResult]


class ReserveFailedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: uuid.UUID
    requested: int
    available: int
    reason: Literal["OUT_OF_STOCK", "INSUFFICIENT_STOCK"]


class ReserveFailureResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reserved: Literal[False] = False
    failed_items: list[ReserveFailedItem]


# --- POST /api/v1/unreserve (canon) ---


class UnreserveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    items: Annotated[list[InventoryItem], Field(min_length=1)]


class UnreserveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: Literal[True] = True


# --- OpenAPI (b2b.yaml) inventory aliases ---


class ReserveOpenAPIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    order_id: uuid.UUID
    items: Annotated[list[InventoryItem], Field(min_length=1)]


class ReserveOpenAPIResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    status: Literal["RESERVED"] = "RESERVED"
    reserved_at: datetime


class UnreserveOpenAPIResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: uuid.UUID
    status: Literal["UNRESERVED"] = "UNRESERVED"
    processed_at: datetime
