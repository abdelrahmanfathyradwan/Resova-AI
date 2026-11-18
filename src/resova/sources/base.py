"""Opportunity-source interface (Phase 1 / Step 2.7).

A *source* is a channel that tells Resova about a freelance opportunity. Only two
channels are permitted (see ``docs/PROJECT_RULES.md``): the client's **own** platform
notification emails, and **manual paste** of a project's text. There is no scraping, no
bypassing of authentication / bot protection / rate limits, no third-party scraping
service, and Resova never fetches a platform project URL by itself.

This module defines the contract and the unified project model that every source emits.
The concrete Mostaql / Khamsat email parsers arrive in a later phase, once the client
supplies real sample alert emails (``TODO(unverified)``).
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from resova.db.models import Platform, SourceKind
from resova.db.repositories import ProjectUpsert

#: The unified project model every source emits. It matches the ``projects`` table and
#: is the very same type the repository consumes, so there is one definition only.
UnifiedProject = ProjectUpsert


class SourceStatus(enum.StrEnum):
    """Implementation status of a source, quoted verbatim in ``docs/SOURCES.md``."""

    implemented = "Implemented"
    not_implemented_pending_samples = "Not implemented - pending sample emails"


@dataclass(frozen=True, slots=True)
class SourceCapabilities:
    """What a source can genuinely do. Declarative only - it never triggers a request."""

    platform: Platform
    supported_kinds: frozenset[SourceKind]

    def supports(self, kind: SourceKind) -> bool:
        """Whether this source handles the given acquisition channel."""
        return kind in self.supported_kinds


@dataclass(frozen=True, slots=True)
class RawProject:
    """An item exactly as a source received it, before normalization.

    Every field except ``source_kind`` is optional because alert emails are often
    partial; missing values must stay ``None`` and are never invented.
    """

    source_kind: SourceKind
    title: str | None = None
    description: str | None = None
    url: str | None = None
    external_id: str | None = None
    published_at: datetime | None = None
    #: Platform-specific leftovers kept for a later phase; never used for matching yet.
    extras: Mapping[str, object] = field(default_factory=dict)


class BaseSource(ABC):
    """Contract every opportunity source must satisfy.

    Implementations must be side-effect free until :meth:`fetch_new` is called, and must
    never fetch a platform project URL.
    """

    @property
    @abstractmethod
    def platform(self) -> Platform:
        """The platform this source belongs to."""

    @property
    @abstractmethod
    def status(self) -> SourceStatus:
        """Implementation status, quoted exactly in ``docs/SOURCES.md``."""

    @abstractmethod
    def capabilities(self) -> SourceCapabilities:
        """Declare which :class:`SourceKind` channels this source handles."""

    @abstractmethod
    def fetch_new(self, since: datetime | None = None) -> Sequence[RawProject]:
        """Return items newer than ``since`` (``None`` means "everything available")."""

    @abstractmethod
    def normalize(self, raw: RawProject) -> UnifiedProject | None:
        """Turn a raw item into a unified project, or ``None`` when it is unusable."""

    @abstractmethod
    def health_check(self) -> bool:
        """Cheap, side-effect-free readiness check that sends nothing."""
