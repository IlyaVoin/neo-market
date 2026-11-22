"""Create reserve_operations table

Revision ID: 004
Revises: 003
Create Date: 2026-11-22 00:00:00.000000

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "reserve_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("operation_type", sa.String(length=20), nullable=False),
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
        "ix_reserve_operations_key_type",
        "reserve_operations",
        ["idempotency_key", "operation_type"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_reserve_operations_key_type", table_name="reserve_operations")
    op.drop_table("reserve_operations")
