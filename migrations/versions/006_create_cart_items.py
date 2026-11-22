"""Create cart_items table

Revision ID: 006
Revises: 005
Create Date: 2026-11-22 02:30:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "cart_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity >= 1", name="ck_cart_quantity"),
        sa.CheckConstraint(
            "(user_id IS NOT NULL) OR (session_id IS NOT NULL)", name="ck_cart_identity"
        ),
    )
    op.create_index(
        "idx_cart_user_sku",
        "cart_items",
        ["user_id", "sku_id"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "idx_cart_session_sku",
        "cart_items",
        ["session_id", "sku_id"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_cart_session_sku", table_name="cart_items")
    op.drop_index("idx_cart_user_sku", table_name="cart_items")
    op.drop_table("cart_items")
