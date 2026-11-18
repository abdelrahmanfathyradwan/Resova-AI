"""Data-access layer for projects (Phase 1 / Step 2.5).

Everything that touches the ``projects`` table lives here so the rest of the code never
writes SQL directly. The centrepiece is :meth:`ProjectRepository.upsert_project`: an
idempotent, portable upsert built on the three nullable UNIQUE dedup keys.

Upsert contract (approved decision):

* Compute every available key and look **all** of them up at once.
* **No** matching row  -> insert.
* **One** matching row -> update its mutable fields and **backfill** any missing keys
  (e.g. a manual paste first, then an email that supplies url/external id).
* **Several** rows     -> never crash: log ``WARNING``, keep the earliest row (lowest
  ``id``), never violate a UNIQUE constraint and record the conflict on the result.
* A unique-key race (``IntegrityError``) rolls back to a savepoint and re-reads inside
  the transaction.

Keys are write-once: an existing non-NULL key is never overwritten. That keeps every
project anchored to the identity it was first learned by, and guarantees the UNIQUE
constraints can never be violated by an update.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from resova.db.base import utcnow
from resova.db.models import Platform, Project, SourceKind
from resova.utils.identity import content_key, external_key, url_key
from resova.utils.normalize import normalize_url

LOGGER = logging.getLogger(__name__)

#: The three portable dedup key columns, in a fixed order.
KEY_ATTRS: tuple[str, ...] = ("url_key", "external_key", "content_key")

#: Non-identity fields refreshed on every successful match (last non-NULL value wins).
#: ``url`` / ``external_id`` are handled with their keys, never here, so a project's
#: URL text always stays consistent with its ``url_key``.
MUTABLE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "budget_min",
    "budget_max",
    "budget_currency",
    "budget_type",
    "duration",
    "category",
    "source_section",
    "comments_count",
    "offers_count",
    "published_at",
)


class UpsertOutcome(enum.StrEnum):
    """What :meth:`ProjectRepository.upsert_project` actually did."""

    created = "created"
    updated = "updated"
    conflict = "conflict"


@dataclass(frozen=True, slots=True)
class ProjectUpsert:
    """Validated input for an upsert; matches the ``projects`` table columns.

    Every field except ``platform``, ``source_kind`` and ``title`` is optional because
    alert emails are frequently partial. Missing data is stored as ``NULL`` - never
    invented (see ``docs/PROJECT_RULES.md``).
    """

    platform: Platform
    source_kind: SourceKind
    title: str
    external_id: str | None = None
    url: str | None = None
    description: str | None = None
    budget_min: float | None = None
    budget_max: float | None = None
    budget_currency: str | None = None
    budget_type: str | None = None
    duration: str | None = None
    category: str | None = None
    source_section: str | None = None
    comments_count: int | None = None
    offers_count: int | None = None
    published_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UpsertResult:
    """The row that now owns the project plus what happened to it."""

    project: Project
    outcome: UpsertOutcome
    matched_row_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class LatencyStats:
    """Discovery latency: how long after publication Resova first saw a project."""

    count: int = 0
    average_seconds: float | None = None
    minimum_seconds: float | None = None
    maximum_seconds: float | None = None


class ProjectRepository:
    """Repository exposing project reads and the idempotent upsert."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # --- identity ---------------------------------------------------------------
    def compute_keys(self, data: ProjectUpsert) -> dict[str, str | None]:
        """Compute all three dedup keys; any of them may be ``None``."""
        return {
            "url_key": url_key(data.url),
            "external_key": external_key(data.platform, data.external_id),
            "content_key": content_key(data.title, data.description),
        }

    def find_by_keys(self, keys: dict[str, str | None]) -> list[Project]:
        """Return every project matching **any** non-NULL key, oldest first."""
        conditions = [
            getattr(Project, name) == value for name, value in keys.items() if value is not None
        ]
        if not conditions:
            return []
        stmt = select(Project).where(or_(*conditions)).order_by(Project.id)
        return list(self._session.scalars(stmt))

    # --- upsert -----------------------------------------------------------------
    def upsert_project(self, data: ProjectUpsert) -> UpsertResult:
        """Idempotently insert or update the project described by ``data``."""
        keys = self.compute_keys(data)
        matched = self.find_by_keys(keys)

        if not matched:
            return self._insert(data, keys)
        if len(matched) == 1:
            return self._update(matched[0], data, keys, outcome=UpsertOutcome.updated)
        return self._resolve_conflict(matched, data, keys)

    def _insert(self, data: ProjectUpsert, keys: dict[str, str | None]) -> UpsertResult:
        project = Project(
            platform=data.platform,
            source_kind=data.source_kind,
            external_id=data.external_id,
            url=data.url,
            normalized_url=normalize_url(data.url),
            title=data.title,
            description=data.description,
            budget_min=data.budget_min,
            budget_max=data.budget_max,
            budget_currency=data.budget_currency,
            budget_type=data.budget_type,
            duration=data.duration,
            category=data.category,
            source_section=data.source_section,
            comments_count=data.comments_count,
            offers_count=data.offers_count,
            published_at=data.published_at,
            first_seen_at=utcnow(),
            last_seen_at=utcnow(),
            url_key=keys["url_key"],
            external_key=keys["external_key"],
            content_key=keys["content_key"],
        )
        try:
            # SAVEPOINT so a race only rolls back this insert, not the caller's work.
            with self._session.begin_nested():
                self._session.add(project)
                self._session.flush()
        except IntegrityError:
            # ``begin_nested`` already restored the session snapshot, so the pending
            # insert is gone and the session is reusable for the re-read below.
            matched = self.find_by_keys(keys)
            if not matched:
                raise
            LOGGER.warning(
                "dedup race recovered for keys %s; reusing existing project id(s) %s",
                _redact_keys(keys),
                [row.id for row in matched],
            )
            if len(matched) == 1:
                return self._update(matched[0], data, keys, outcome=UpsertOutcome.updated)
            return self._resolve_conflict(matched, data, keys)
        return UpsertResult(project=project, outcome=UpsertOutcome.created)

    def _update(
        self,
        project: Project,
        data: ProjectUpsert,
        keys: dict[str, str | None],
        *,
        outcome: UpsertOutcome,
    ) -> UpsertResult:
        self._apply_scalar_fields(project, data)
        self._backfill_keys(project, data, keys, blocked=frozenset())
        project.last_seen_at = utcnow()
        self._session.flush()
        return UpsertResult(project=project, outcome=outcome)

    def _resolve_conflict(
        self, matched: list[Project], data: ProjectUpsert, keys: dict[str, str | None]
    ) -> UpsertResult:
        kept = min(matched, key=lambda row: row.id)
        others = [row for row in matched if row is not kept]
        # Never assign a key that another project already owns, or UNIQUE breaks.
        blocked = {
            value
            for row in others
            for attr in KEY_ATTRS
            if (value := getattr(row, attr)) is not None
        }
        LOGGER.warning(
            "conflicting dedup keys map to multiple projects: keeping earliest id=%s, "
            "ignoring id(s)=%s (keys %s)",
            kept.id,
            [row.id for row in others],
            _redact_keys(keys),
        )
        self._apply_scalar_fields(kept, data)
        self._backfill_keys(kept, data, keys, blocked=blocked)
        kept.last_seen_at = utcnow()
        self._session.flush()
        return UpsertResult(
            project=kept,
            outcome=UpsertOutcome.conflict,
            matched_row_ids=tuple(row.id for row in matched),
        )

    # --- helpers ----------------------------------------------------------------
    @staticmethod
    def _apply_scalar_fields(project: Project, data: ProjectUpsert) -> None:
        for field in MUTABLE_FIELDS:
            value = getattr(data, field)
            if value is not None:
                setattr(project, field, value)

    @staticmethod
    def _backfill_keys(
        project: Project,
        data: ProjectUpsert,
        keys: dict[str, str | None],
        *,
        blocked: frozenset[str],
    ) -> None:
        """Fill in key slots that are still ``NULL`` without ever overwriting one."""
        if (
            project.url_key is None
            and keys["url_key"] is not None
            and keys["url_key"] not in blocked
        ):
            project.url = data.url
            project.normalized_url = normalize_url(data.url)
            project.url_key = keys["url_key"]

        if (
            project.external_key is None
            and keys["external_key"] is not None
            and keys["external_key"] not in blocked
        ):
            project.external_id = data.external_id
            project.external_key = keys["external_key"]

        if (
            project.content_key is None
            and keys["content_key"] is not None
            and keys["content_key"] not in blocked
        ):
            project.content_key = keys["content_key"]

    # --- reporting --------------------------------------------------------------
    def discovery_latency(
        self,
        *,
        since: datetime | None = None,
        platform: Platform | None = None,
    ) -> LatencyStats:
        """Return discovery-latency statistics for projects with a publish date.

        Latency is ``first_seen_at - published_at`` in seconds. Computed in Python from
        a filtered selection on purpose - date arithmetic is done portably instead of
        relying on backend-specific date functions. Negative deltas (a source-reported
        publish date in the future, or clock skew) are ignored.
        """
        conditions = [Project.published_at.is_not(None)]
        if since is not None:
            conditions.append(Project.published_at >= since)
        if platform is not None:
            conditions.append(Project.platform == platform)

        stmt = select(Project.first_seen_at, Project.published_at).where(*conditions)
        # The WHERE clause already excludes NULL publish dates, so both values exist.
        deltas = sorted(
            (first_seen - published).total_seconds()
            for first_seen, published in self._session.execute(stmt)
            if (first_seen - published).total_seconds() >= 0
        )
        if not deltas:
            return LatencyStats()
        return LatencyStats(
            count=len(deltas),
            average_seconds=sum(deltas) / len(deltas),
            minimum_seconds=deltas[0],
            maximum_seconds=deltas[-1],
        )


def _redact_keys(keys: dict[str, str | None]) -> dict[str, str]:
    """Log-safe view of key hashes: presence only, never the full hash."""
    return {name: ("set" if value else "null") for name, value in keys.items()}
