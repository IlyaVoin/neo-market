import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

ModerationEventType = Literal["MODERATED", "BLOCKED"]


class BlockingReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID | None = None
    title: Annotated[str, Field(min_length=1)]
    comment: str | None = None


class FieldReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: Annotated[str, Field(min_length=1)]
    sku_id: uuid.UUID | None = None
    comment: str | None = None


# --- POST /api/v1/events/moderation (canon b2b-flows.md#apply-moderation) ---


class ModerationEventRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    product_id: uuid.UUID
    status: ModerationEventType
    hard_block: bool = False
    blocking_reason: BlockingReasonIn | None = None
    field_reports: list[FieldReportIn] = []


# --- POST /api/v1/moderation/events (OpenAPI b2b.yaml) ---


class ModerationEventOpenAPIRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: uuid.UUID
    product_id: uuid.UUID
    event_type: ModerationEventType
    hard_block: bool = False
    moderator_id: uuid.UUID | None = None
    moderator_comment: str | None = None
    blocking_reason_id: uuid.UUID | None = None
    field_reports: list[FieldReportIn] | None = None
    occurred_at: datetime


# --- POST /api/v1/products/{product_id}/approve (canon moderation-flows.md#approve-product) ---


class ApproveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    moderator_comment: str | None = None


# --- POST /api/v1/products/{product_id}/decline (canon moderation-flows.md MOD-4/MOD-5) ---


class DeclineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    blocking_reason_id: uuid.UUID
    moderator_comment: str | None = None
    field_reports: list[FieldReportIn] = []


# --- POST /api/v1/events/product (canon moderation-flows.md MOD-1: B2B -> Moderation) ---


class IncomingProductEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    seller_id: uuid.UUID
    event: Literal["CREATED", "EDITED", "DELETED"]
    date: datetime | None = None
