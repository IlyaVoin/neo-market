"""Create product_moderation table

Revision ID: 008
Revises: 007
Create Date: 2026-11-22 05:36:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    moderation_status = sa.Enum(
        "PENDING",
        "IN_REVIEW",
        "MODERATED",
        "BLOCKED",
        "HARD_BLOCKED",
        name="moderationstatus",
        native_enum=False,
    )
    op.create_table(
        "product_moderation",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", moderation_status, nullable=False),
        sa.Column("moderator_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("moderator_comment", sa.Text(), nullable=True),
        sa.Column("blocking_reason_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("field_reports", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("event_idempotency_key", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("date_moderation", sa.DateTime(timezone=True), nullable=True),
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
        sa.UniqueConstraint("product_id", name="uq_product_moderation_product_id"),
    )
    op.create_index(
        "ix_product_moderation_status", "product_moderation", ["status"]
    )


def downgrade() -> None:
    op.drop_index("ix_product_moderation_status", table_name="product_moderation")
    op.drop_table("product_moderation")