
from pydantic import BaseModel, ConfigDict


class Error(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    details: dict | None = None
