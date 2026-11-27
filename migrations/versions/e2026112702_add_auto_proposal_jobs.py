"""add auto_proposal_jobs table for idempotent automatic proposal generation

Revision ID: e2026112702
Revises: d2026112701
Create Date: 2026-11-27 10:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2026112702"
down_revision: str | None = "d2026112701"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "auto_proposal_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("owner_user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("draft_id", sa.Integer(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "telegram_notified", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_auto_proposal_jobs_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["telegram_users.id"],
            name=op.f("fk_auto_proposal_jobs_owner_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"],
            ["proposal_drafts.id"],
            name=op.f("fk_auto_proposal_jobs_draft_id_proposal_drafts"),
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "project_id",
            "owner_user_id",
            name="uq_auto_proposal_job_project_owner",
        ),
    )
    op.create_index(
        op.f("ix_auto_proposal_jobs_project_id"),
        "auto_proposal_jobs",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auto_proposal_jobs_owner_user_id"),
        "auto_proposal_jobs",
        ["owner_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_auto_proposal_jobs_status"),
        "auto_proposal_jobs",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_auto_proposal_jobs_status"), table_name="auto_proposal_jobs")
    op.drop_index(op.f("ix_auto_proposal_jobs_owner_user_id"), table_name="auto_proposal_jobs")
    op.drop_index(op.f("ix_auto_proposal_jobs_project_id"), table_name="auto_proposal_jobs")
    op.drop_table("auto_proposal_jobs")
