import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base
from src.models.enums import ModerationStatus


class ProductModeration(Base):
    """Карточка модерации товара (сервис Moderation, сторона эмитента).

    Одна карточка на товар. Статус определяет approve/block flow.
    event_idempotency_key фиксируется при approve и переиспользуется при
    повторной доставке события MODERATED в B2B (защита от двойной публикации).
    """

    __tablename__ = "product_moderation"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("products.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    status: Mapped[ModerationStatus] = mapped_column(
        SQLEnum(ModerationStatus, native_enum=False),
        default=ModerationStatus.PENDING,
        nullable=False,
    )
    moderator_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    moderator_comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    blocking_reason_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    field_reports: Mapped[list] = mapped_column(
        JSONB, default=list, nullable=False, server_default="[]"
    )
    event_idempotency_key: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    date_moderation: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (Index("ix_product_moderation_status", "status"),)
