"""Create skus, sku_characteristics, outbox_events tables

Revision ID: 003
Revises: 002
Create Date: 2026-11-19 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "skus",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("cost_price", sa.Integer(), nullable=False),
        sa.Column("discount", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("image", sa.String(length=1000), nullable=False),
        sa.Column("active_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("reserved_quantity", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skus_product_id", "skus", ["product_id"], unique=False)
    op.create_index("ix_skus_active_quantity", "skus", ["active_quantity"], unique=False)

    op.create_table(
        "sku_characteristics",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sku_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sku_characteristics_sku_id", "sku_characteristics", ["sku_id"], unique=False
    )
    op.create_index(
        "ix_sku_characteristics_name", "sku_characteristics", ["name"], unique=False
    )

    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("service_key", sa.String(length=255), nullable=False),
        sa.Column("target_url", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_events_processed_at", "outbox_events", ["processed_at"], unique=False
    )
    op.create_index(
        "ix_outbox_events_event_type", "outbox_events", ["event_type"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_event_type", table_name="outbox_events")
    op.drop_index("ix_outbox_events_processed_at", table_name="outbox_events")
    op.drop_table("outbox_events")
    op.drop_index("ix_sku_characteristics_name", table_name="sku_characteristics")
    op.drop_index("ix_sku_characteristics_sku_id", table_name="sku_characteristics")
    op.drop_table("sku_characteristics")
    op.drop_index("ix_skus_active_quantity", table_name="skus")
    op.drop_index("ix_skus_product_id", table_name="skus")
    op.drop_table("skus")