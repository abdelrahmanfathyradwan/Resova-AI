"""add telegram_users table for multi-user access control and owner management

Revision ID: c2026112302
Revises: b2026112301
Create Date: 2026-11-23 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c2026112302"
down_revision: str | None = "b2026112301"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "telegram_users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column(
            "role",
            sa.Enum("owner", "admin", "user", name="telegramuserrole", native_enum=False, length=32),
            nullable=False,
            server_default="user",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "active",
                "rejected",
                "disabled",
                name="telegramuserstatus",
                native_enum=False,
                length=32,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("registered_at", sa.DateTime(), nullable=False),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("approved_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_telegram_users_telegram_chat_id",
        "telegram_users",
        ["telegram_chat_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_telegram_users_telegram_chat_id", table_name="telegram_users")
    op.drop_table("telegram_users")
