"""add confidence to project_analyses and last_activity_at to projects

Revision ID: b2026112301
Revises: a593857c8df8
Create Date: 2026-11-23 11:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2026112301"
down_revision: str | None = "a593857c8df8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("projects", sa.Column("last_activity_at", sa.DateTime(), nullable=True))
    op.add_column("project_analyses", sa.Column("confidence", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("project_analyses", "confidence")
    op.drop_column("projects", "last_activity_at")
