"""Database layer: SQLAlchemy models, session/engine wiring and repositories.

Implemented in Phase 1:
- Step 2.4: models, engine/session wiring and Alembic migrations.
- Step 2.5: repositories (idempotent ``upsert_project`` and the latency query).

Portability rule: portable SQLAlchemy only. No SQLite-specific SQL and no
vendor-specific features, so switching to PostgreSQL is a configuration change.
"""

from resova.db.base import Base, TimestampMixin, utcnow
from resova.db.models import (
    DraftStatus,
    Notification,
    NotificationStatus,
    Platform,
    Project,
    ProjectAnalysis,
    ProposalDraft,
    ScoreBand,
    SourceKind,
    SourceMessage,
    SyncLog,
    SyncStatus,
    SystemSetting,
    UserAction,
    UserProfile,
)
from resova.db.session import (
    create_db_engine,
    create_schema,
    create_session_factory,
    session_scope,
)

__all__ = [
    "Base",
    "DraftStatus",
    "Notification",
    "NotificationStatus",
    "Platform",
    "Project",
    "ProjectAnalysis",
    "ProposalDraft",
    "ScoreBand",
    "SourceKind",
    "SourceMessage",
    "SyncLog",
    "SyncStatus",
    "SystemSetting",
    "TimestampMixin",
    "UserAction",
    "UserProfile",
    "create_db_engine",
    "create_schema",
    "create_session_factory",
    "session_scope",
    "utcnow",
]
