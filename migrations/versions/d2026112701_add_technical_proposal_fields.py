"""add technical proposal generator fields and user_professional_profiles table

Revision ID: d2026112701
Revises: c2026112302
Create Date: 2026-11-27 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2026112701"
down_revision: str | None = "c2026112302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_professional_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("professional_name", sa.String(length=255), nullable=True),
        sa.Column("specialization", sa.Text(), nullable=True),
        sa.Column("skills_services", sa.Text(), nullable=True),
        sa.Column("portfolio_samples", sa.Text(), nullable=True),
        sa.Column("tools_software", sa.Text(), nullable=True),
        sa.Column("communication_style", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["telegram_users.id"],
            name=op.f("fk_user_professional_profiles_user_id_telegram_users"),
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_user_professional_profiles_user_id"),
        "user_professional_profiles",
        ["user_id"],
        unique=True,
    )

    with op.batch_alter_table("proposal_drafts") as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("title", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("sections_json", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("timeline_text", sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column("cost_text", sa.String(length=256), nullable=True))
        batch_op.add_column(
            sa.Column(
                "editor_status",
                sa.Enum(
                    "draft",
                    "edited",
                    "ready",
                    name="proposaleditorstatus",
                    native_enum=False,
                    length=32,
                ),
                nullable=False,
                server_default="draft",
            )
        )
        batch_op.add_column(
            sa.Column(
                "is_ai_generated",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.add_column(sa.Column("ai_model", sa.String(length=128), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_proposal_drafts_owner_user_id_telegram_users"),
            "telegram_users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index(
            op.f("ix_proposal_drafts_owner_user_id"),
            ["owner_user_id"],
            unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("proposal_drafts") as batch_op:
        batch_op.drop_index(op.f("ix_proposal_drafts_owner_user_id"))
        batch_op.drop_constraint(
            op.f("fk_proposal_drafts_owner_user_id_telegram_users"),
            type_="foreignkey",
        )
        batch_op.drop_column("ai_model")
        batch_op.drop_column("is_ai_generated")
        batch_op.drop_column("editor_status")
        batch_op.drop_column("cost_text")
        batch_op.drop_column("timeline_text")
        batch_op.drop_column("sections_json")
        batch_op.drop_column("title")
        batch_op.drop_column("owner_user_id")

    op.drop_index(
        op.f("ix_user_professional_profiles_user_id"),
        table_name="user_professional_profiles",
    )
    op.drop_table("user_professional_profiles")
