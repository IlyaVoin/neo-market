"""Create orders and order_items tables

Revision ID: 007
Revises: 006
Create Date: 2026-11-22 04:10:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    order_status = sa.Enum(
        "CREATED",
        "PAID",
        "ASSEMBLING",
        "DELIVERING",
        "DELIVERED",
        "CANCELLED",
        "CANCEL_PENDING",
        name="orderstatus",
        native_enum=False,
    )
    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("buyer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", order_status, nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("delivery_address", sa.Text(), nullable=True),
        sa.Column("total_amount", sa.Integer(), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_orders_idempotency_key",
        "orders",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index("ix_orders_buyer_id", "orders", ["buyer_id"])

    op.create_table(
        "order_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_title", sa.String(length=255), nullable=False),
        sa.Column("sku_name", sa.String(length=255), nullable=False),
        sa.Column("image_url", sa.String(length=1000), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Integer(), nullable=False),
        sa.Column("line_total", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("quantity >= 1", name="ck_order_item_quantity"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_buyer_id", table_name="orders")
    op.drop_index("uq_orders_idempotency_key", table_name="orders")
    op.drop_table("orders")