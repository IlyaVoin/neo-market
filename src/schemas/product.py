import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class Error(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict | None = None


class ProductImageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: Annotated[str, Field(min_length=1, max_length=1000)]
    ordering: Annotated[int, Field(ge=0)] = 0


class ProductCharacteristicCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    value: Annotated[str, Field(min_length=1)]


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Annotated[str, Field(min_length=1, max_length=255)]
    description: Annotated[str, Field(min_length=1, max_length=5000)]
    category_id: uuid.UUID
    images: Annotated[list[ProductImageCreate], Field(min_length=1)]
    characteristics: list[ProductCharacteristicCreate] = []


class ProductImageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    url: str
    ordering: int


class ProductCharacteristicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    name: str
    value: str


class ProductResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    seller_id: uuid.UUID
    category_id: uuid.UUID
    title: str
    slug: str | None
    description: str
    status: str
    deleted: bool
    blocking_reason_id: uuid.UUID | None
    moderator_comment: str | None
    images: list[ProductImageResponse]
    characteristics: list[ProductCharacteristicResponse]
    skus: list = []
    created_at: datetime
    updated_at: datetime
