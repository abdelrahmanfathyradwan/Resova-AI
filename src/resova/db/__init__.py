"""Database layer: SQLAlchemy models, session/engine wiring and repositories.

NOT IMPLEMENTED - Phase 1 / Step 2.4 (Database + Alembic migrations).

Planned content:
- Declarative models for projects, source_messages, project_analyses, notifications,
  user_actions, proposal_drafts, sync_logs, user_profile and system_settings.
- SQLite connection setup with ``foreign_keys=ON`` and WAL journal mode.
- One repository per aggregate, with transactions where consistency matters, an
  idempotent ``upsert_project`` and a latency query across the recorded timestamps.

Portability rule: portable SQLAlchemy only. No SQLite-specific SQL and no
vendor-specific features, so switching to PostgreSQL is a configuration change.
"""

__all__: list[str] = []
