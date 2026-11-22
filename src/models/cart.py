import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.database import Base


class CartItem(Base):
    """Позиция корзины B2C.

    Хранит минимум: sku_id + quantity + идентичность (user_id или session_id).
    Данные товара (цена, наличие) НЕ хранятся — обогащаются из B2B при каждом GET.
    """

    __tablename__ = "cart_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    session_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sku_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("skus.id", ondelete="CASCADE"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
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

    __table_args__ = (
        CheckConstraint("quantity >= 1", name="ck_cart_quantity"),
        CheckConstraint(
            "(user_id IS NOT NULL) OR (session_id IS NOT NULL)", name="ck_cart_identity"
        ),
        Index(
            "idx_cart_user_sku",
            "user_id",
            "sku_id",
            unique=True,
            sqlite_where=text("user_id IS NOT NULL"),
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "idx_cart_session_sku",
            "session_id",
            "sku_id",
            unique=True,
            sqlite_where=text("session_id IS NOT NULL"),
            postgresql_where=text("session_id IS NOT NULL"),
        ),
    )
