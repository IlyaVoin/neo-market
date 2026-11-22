import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class ReserveOperation(Base):
    """Идемпотентные операции резервирования / снятия резерва.

    Ключ идемпотентности:
    - для reserve -- idempotency_key из запроса B2C;
    - для unreserve -- order_id заказа.

    operation_type разделяет reserve/unreserve, чтобы одинаковые UUID не
    коллизировали между разными типами операций.
    """

    __tablename__ = "reserve_operations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    idempotency_key: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    operation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=datetime.utcnow,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_reserve_operations_key_type", "idempotency_key", "operation_type", unique=True),
    )
