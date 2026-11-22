import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ProductBlockingReason(Base):
    """Справочник причин блокировки (flow MOD-6).

    hard_block=true → жёсткая блокировка (терминальный HARD_BLOCKED);
    hard_block=false → мягкая блокировка (BLOCKED).
    """

    __tablename__ = "product_blocking_reasons"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    hard_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
