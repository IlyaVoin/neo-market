import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ProcessedEvent(Base):
    """Идемпотентность обработки входящих событий Moderation.

    Уникальный idempotency_key гарантирует, что повторная обработка того же
    события не приводит к повторным побочным эффектам (статус/каскад в B2C).
    """

    __tablename__ = "processed_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    sender_service: Mapped[str] = mapped_column(String(100), default="moderation", nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_processed_events_idempotency_key", "idempotency_key", unique=True),
    )
