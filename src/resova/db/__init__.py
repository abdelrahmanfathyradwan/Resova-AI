"""Database layer: SQLAlchemy models, session/engine wiring and repositories.

Implemented in Phase 1:
- Step 2.4: models, engine/session wiring and Alembic migrations.
- Step 2.5: repositories (idempotent ``upsert_project`` and the latency query).

Portability rule: portable SQLAlchemy only. No SQLite-specific SQL and no
vendor-specific features, so switching to PostgreSQL is a configuration change.
"""

from resova.db.base import Base, TimestampMixin, utcnow
from resova.db.models import (
    AutoProposalJob,
    AutoProposalJobStatus,
    DraftStatus,
    Notification,
    NotificationStatus,
    Platform,
    Project,
    ProjectAnalysis,
    ProposalAccessToken,
    ProposalDraft,
    ProposalEditorStatus,
    ScoreBand,
    SourceKind,
    SourceMessage,
    SyncLog,
    SyncStatus,
    SystemSetting,
    TelegramUser,
    UserAction,
    UserProfessionalProfile,
    UserProfile,
)
from resova.db.repositories import (
    LatencyStats,
    ProjectRepository,
    ProjectUpsert,
    UpsertOutcome,
    UpsertResult,
)
from resova.db.session import (
    create_db_engine,
    create_schema,
    create_session_factory,
    session_scope,
)

__all__ = [
    "AutoProposalJob",
    "AutoProposalJobStatus",
    "Base",
    "DraftStatus",
    "LatencyStats",
    "Notification",
    "NotificationStatus",
    "Platform",
    "Project",
    "ProjectAnalysis",
    "ProjectRepository",
    "ProjectUpsert",
    "ProposalAccessToken",
    "ProposalDraft",
    "ProposalEditorStatus",
    "ScoreBand",
    "SourceKind",
    "SourceMessage",
    "SyncLog",
    "SyncStatus",
    "SystemSetting",
    "TelegramUser",
    "TimestampMixin",
    "UpsertOutcome",
    "UpsertResult",
    "UserAction",
    "UserProfessionalProfile",
    "UserProfile",
    "create_db_engine",
    "create_schema",
    "create_session_factory",
    "session_scope",
    "utcnow",
]
