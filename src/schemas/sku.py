import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field


class SKUCharacteristicCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=255)]
    value: Annotated[str, Field(min_length=1)]


class SKUCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=255)]
    price: Annotated[int, Field(gt=0, description="Price in kopecks")]
    cost_price: Annotated[int, Field(gt=0, description="Cost price in kopecks")]
    discount: Annotated[int, Field(ge=0, description="Discount in kopecks")] = 0
    image: Annotated[str, Field(min_length=1, max_length=1000)]
    characteristics: list[SKUCharacteristicCreate] = []


class SKUCharacteristicResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    name: str
    value: str


class SKUResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    price: int
    cost_price: int
    discount: int
    image: str
    active_quantity: int
    reserved_quantity: int
    characteristics: list[SKUCharacteristicResponse]
    created_at: datetime
    updated_at: datetime