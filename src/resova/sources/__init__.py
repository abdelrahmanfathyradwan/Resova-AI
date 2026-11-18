"""Opportunity sources (Phase 1 / Step 2.7).

This package defines the *source contract* only: the :class:`BaseSource` interface, the
unified project model and the capability/status descriptors. Concrete Mostaql and Khamsat
sources are **not implemented yet** - they wait for real sample alert emails. Their exact
status labels are recorded in ``docs/SOURCES.md``.

Compliance: discovery is limited to the client's own platform notification emails and
manual paste of project text. No scraping, no bypassing of authentication, bot protection
or rate limits, no session cookies, no third-party scraping services and no automatic
fetching of platform project URLs (see ``docs/PROJECT_RULES.md``).
"""

from resova.sources.base import (
    BaseSource,
    RawProject,
    SourceCapabilities,
    SourceStatus,
    UnifiedProject,
)

__all__ = [
    "BaseSource",
    "RawProject",
    "SourceCapabilities",
    "SourceStatus",
    "UnifiedProject",
]
