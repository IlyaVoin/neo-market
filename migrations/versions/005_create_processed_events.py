"""Create processed_events table and add moderation fields to products

Revision ID: 005
Revises: 004
Create Date: 2026-11-22 01:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "processed_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "sender_service",
            sa.String(length=100),
            nullable=False,
            server_default=sa.text("'moderation'"),
        ),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_processed_events_idempotency_key",
        "processed_events",
        ["idempotency_key"],
        unique=True,
    )

    op.add_column(
        "products",
        sa.Column("blocking_reason_title", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column(
            "field_reports",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    op.drop_column("products", "field_reports")
    op.drop_column("products", "blocking_reason_title")
    op.drop_index("ix_processed_events_idempotency_key", table_name="processed_events")
    op.drop_table("processed_events")
