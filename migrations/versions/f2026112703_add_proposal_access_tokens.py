"""add proposal_access_tokens table for direct Telegram proposal access

Revision ID: f2026112703
Revises: e2026112702
Create Date: 2026-11-27 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f2026112703"
down_revision: str | None = "e2026112702"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "proposal_access_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column(
            "revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["proposal_id"],
            ["proposal_drafts.id"],
            name=op.f("fk_proposal_access_tokens_proposal_id_proposal_drafts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_proposal_access_tokens_owner_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_proposal_access_tokens_token_hash"),
        "proposal_access_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_proposal_access_tokens_proposal_id"),
        "proposal_access_tokens",
        ["proposal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_access_tokens_owner_user_id"),
        "proposal_access_tokens",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_proposal_access_tokens_expires_at"),
        "proposal_access_tokens",
        ["expires_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_proposal_access_tokens_expires_at"), table_name="proposal_access_tokens"
    )
    op.drop_index(
        op.f("ix_proposal_access_tokens_owner_user_id"), table_name="proposal_access_tokens"
    )
    op.drop_index(
        op.f("ix_proposal_access_tokens_proposal_id"), table_name="proposal_access_tokens"
    )
    op.drop_index(
        op.f("ix_proposal_access_tokens_token_hash"), table_name="proposal_access_tokens"
    )
    op.drop_table("proposal_access_tokens")
