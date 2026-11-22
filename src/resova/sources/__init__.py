"""Opportunity sources (Phase 1 / Step 2.7).

This package defines the source contract and concrete opportunity discovery sources:
- ListingPoller: polls public listing pages for Mostaql & Khamsat (disabled by default).
- EmailSource: ingests client platform alert emails via IMAP (disabled by default).
"""

from resova.sources.base import (
    BaseSource,
    RawProject,
    SourceCapabilities,
    SourceStatus,
    UnifiedProject,
)
from resova.sources.email_source import EmailSource, parse_email_message
from resova.sources.listing_poller import ListingPoller

__all__ = [
    "BaseSource",
    "EmailSource",
    "ListingPoller",
    "RawProject",
    "SourceCapabilities",
    "SourceStatus",
    "UnifiedProject",
    "parse_email_message",
]
