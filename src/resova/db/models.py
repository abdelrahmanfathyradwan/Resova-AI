"""SQLAlchemy 2.x ORM models (Phase 1 / Step 2.4).

Schema is exactly **nine** tables (plus Alembic's own ``alembic_version``):

1. ``projects``          - one normalized freelance opportunity
2. ``source_messages``   - raw ingested alert emails (deduplicated by Message-ID)
3. ``project_analyses``  - AI scoring results, with an explicit ``analyzed_at``
4. ``notifications``     - Telegram messages sent (deduplicates sends)
5. ``user_actions``      - what the human did with a project
6. ``proposal_drafts``   - editable proposal drafts (never sent automatically)
7. ``sync_logs``         - per-source poll/sync runs
8. ``user_profile``      - versioned client-profile snapshots
9. ``system_settings``   - runtime-editable key/value overrides

Portable dedup strategy (agreed decision, see DECISIONS.md): ``projects`` carries three
nullable, unique dedup keys - ``url_key``, ``external_key`` and ``content_key``. Multiple
NULLs are allowed on every backend, so a NULL key simply never matches. The values are
computed in the application layer (``resova.utils.identity``), never in SQL.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from resova.db.base import Base, TimestampMixin, utcnow


class Platform(enum.StrEnum):
    """Where an opportunity came from. ``manual`` is used when the site is unknown."""

    mostaql = "mostaql"
    khamsat = "khamsat"
    manual = "manual"


class SourceKind(enum.StrEnum):
    """How Resova learned about the opportunity (never by scraping)."""

    alert_email = "alert_email"
    manual_paste = "manual_paste"
    website_page = "website_page"


class NotificationStatus(enum.StrEnum):
    pending = "pending"
    pending_digest = "pending_digest"
    sent = "sent"
    failed = "failed"
    skipped = "skipped"


class SyncStatus(enum.StrEnum):
    running = "running"
    success = "success"
    failed = "failed"


class DraftStatus(enum.StrEnum):
    draft = "draft"
    approved = "approved"
    discarded = "discarded"


class ScoreBand(enum.StrEnum):
    strong = "strong"
    good = "good"
    moderate = "moderate"
    weak = "weak"
    below = "below"


class TelegramUserRole(enum.StrEnum):
    owner = "owner"
    admin = "admin"
    user = "user"


class TelegramUserStatus(enum.StrEnum):
    pending = "pending"
    active = "active"
    rejected = "rejected"
    disabled = "disabled"


def _enum(enum_type: type[enum.Enum], length: int = 32) -> Enum:
    """A portable (VARCHAR + CHECK) enum column type - no native enum types."""
    return Enum(enum_type, native_enum=False, length=length, validate_strings=True)


class Project(Base, TimestampMixin):
    """A single normalized freelance opportunity."""

    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)

    platform: Mapped[Platform] = mapped_column(_enum(Platform), nullable=False, index=True)
    source_kind: Mapped[SourceKind] = mapped_column(_enum(SourceKind), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    normalized_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # Alert emails are often partial: description and every budget field are nullable.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Budget is NEVER an exclusion criterion (PROJECT_RULES.md). Informational only.
    budget_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    budget_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    duration: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # Added in Step 2.4: optional source-provided metadata.
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_section: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Competition signals - only ever shown when the source itself provides them.
    comments_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    offers_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_activity_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    # Portable dedup keys: nullable + unique (multiple NULLs never collide).
    url_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    external_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    content_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)

    analyses: Mapped[list[ProjectAnalysis]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    actions: Mapped[list[UserAction]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    drafts: Mapped[list[ProposalDraft]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "comments_count IS NULL OR comments_count >= 0",
            name="comments_count_non_negative",
        ),
        CheckConstraint(
            "offers_count IS NULL OR offers_count >= 0",
            name="offers_count_non_negative",
        ),
    )


class SourceMessage(Base, TimestampMixin):
    """A raw alert email ingested from the client's own mailbox."""

    __tablename__ = "source_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[Platform] = mapped_column(_enum(Platform), nullable=False, index=True)
    #: RFC 5322 ``Message-ID``; the stable idempotency key for email ingestion.
    message_id: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    received_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    subject: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ProjectAnalysis(Base, TimestampMixin):
    """One AI scoring result for a project."""

    __tablename__ = "project_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    track: Mapped[str | None] = mapped_column(String(64), nullable=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    band: Mapped[ScoreBand | None] = mapped_column(_enum(ScoreBand), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Explicit timestamp of the analysis itself (not just the row's created_at).
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="analyses")

    __table_args__ = (CheckConstraint("score >= 0 AND score <= 100", name="score_range"),)


class Notification(Base, TimestampMixin):
    """A notification sent to the human (currently Telegram only)."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="telegram")
    status: Mapped[NotificationStatus] = mapped_column(
        _enum(NotificationStatus), nullable=False, default=NotificationStatus.pending
    )
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    project: Mapped[Project] = relationship(back_populates="notifications")


class UserAction(Base, TimestampMixin):
    """Something the human did with a project (never an automatic action)."""

    __tablename__ = "user_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="actions")


class ProposalDraft(Base, TimestampMixin):
    """An editable proposal draft. Drafts are never sent automatically."""

    __tablename__ = "proposal_drafts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="ar")
    status: Mapped[DraftStatus] = mapped_column(
        _enum(DraftStatus), nullable=False, default=DraftStatus.draft
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    generated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    project: Mapped[Project] = relationship(back_populates="drafts")


class SyncLog(Base, TimestampMixin):
    """One poll/sync run for a source."""

    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    platform: Mapped[Platform] = mapped_column(_enum(Platform), nullable=False, index=True)
    status: Mapped[SyncStatus] = mapped_column(_enum(SyncStatus), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    items_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_updated: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class UserProfile(Base, TimestampMixin):
    """An immutable snapshot of the client profile (version + content hash)."""

    __tablename__ = "user_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class SystemSetting(Base, TimestampMixin):
    """Runtime-editable key/value override (used by [runtime] settings)."""

    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)


class TelegramUser(Base, TimestampMixin):
    """A registered Telegram user with role and approval status."""

    __tablename__ = "telegram_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, index=True, nullable=False
    )
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[TelegramUserRole] = mapped_column(
        _enum(TelegramUserRole), nullable=False, default=TelegramUserRole.user
    )
    status: Mapped[TelegramUserStatus] = mapped_column(
        _enum(TelegramUserStatus), nullable=False, default=TelegramUserStatus.pending
    )
    registered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    approved_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
