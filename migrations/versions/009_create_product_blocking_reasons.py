"""Create product_blocking_reasons table and seed reasons

Revision ID: 009
Revises: 008
Create Date: 2026-11-22 06:03:00.000000

"""
import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


_REASONS = [
    ("OUTDATED_INFO", "Описание не соответствует товару", False),
    ("LOW_QUALITY_IMAGES", "Изображение не соответствует товару", False),
    ("WRONG_CATEGORY", "Некорректная категория товара", False),
    ("NOT_ENOUGH_INFO", "Недостаточно информации о товаре", False),
    ("PROFANITY", "Нецензурные или оскорбительные материалы", False),
    ("DUPLICATE", "Дублирование существующего товара", False),
    ("WRONG_PRICE", "Некорректная цена", False),
    ("COUNTERFEIT", "Контрафактный товар", True),
    ("FORBIDDEN_GOODS", "Товар запрещён к продаже на территории РФ", True),
    ("COPYRIGHT", "Товар нарушает авторские права", True),
]


def upgrade() -> None:
    op.create_table(
        "product_blocking_reasons",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("hard_block", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_product_blocking_reasons_code"),
    )

    table = sa.table(
        "product_blocking_reasons",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String),
        sa.column("title", sa.String),
        sa.column("hard_block", sa.Boolean),
    )
    op.bulk_insert(
        table,
        [
            {"id": uuid.uuid4(), "code": code, "title": title, "hard_block": hard_block}
            for code, title, hard_block in _REASONS
        ],
    )


def downgrade() -> None:
    op.drop_table("product_blocking_reasons")