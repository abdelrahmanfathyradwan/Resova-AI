"""Dedup identity keys for projects (Phase 1 / Step 2.5).

Three portable, independently-optional keys are computed in the application layer and
stored on ``projects`` as nullable UNIQUE columns:

* ``url_key``      - the normalized URL (see :func:`resova.utils.normalize.normalize_url`).
* ``external_key`` - ``platform`` + the platform's own id, when the source provides one.
* ``content_key``  - the normalized title **plus** the normalized description.

Design rule (approved decision): a *false* duplicate silently hides a real opportunity,
while a *false* new row only costs one extra notification. So ``content_key`` is only
computed when a **substantive description** is present (at least
:data:`MIN_DESCRIPTION_LENGTH` characters *after* normalization); a short or missing
description yields ``NULL``. Titles are generic and repeat often, therefore a title-only
or budget-only match is never treated as a duplicate.

Platform-specific id extraction from URLs is deliberately **not** attempted here; it
waits for real sample emails in Phase 3 (``TODO(unverified)``).
"""

from __future__ import annotations

import hashlib

from resova.utils.normalize import normalize_text, normalize_url

#: Minimum normalized description length before content-based dedup applies.
MIN_DESCRIPTION_LENGTH = 40

#: Prefixes keep the three key spaces visually distinct in logs and in the database.
_URL_PREFIX = "url:"
_EXTERNAL_PREFIX = "ext:"
_CONTENT_PREFIX = "content:"


def _sha256(value: str) -> str:
    """Stable 64-character sha256 hex digest (matches the ``String(64)`` columns)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def url_key(url: str | None) -> str | None:
    """Return the dedup key for ``url``, or ``None`` when it is missing/blank."""
    normalized = normalize_url(url)
    if normalized is None:
        return None
    return _sha256(f"{_URL_PREFIX}{normalized}")


def external_key(platform: object, external_id: str | None) -> str | None:
    """Return the dedup key for a platform-native id, or ``None`` when absent.

    ``platform`` is intentionally typed loosely so an ``enum.StrEnum`` or a plain
    string both work. Normalizing the id folds Arabic variants and collapses spaces.
    """
    identifier = normalize_text(external_id)
    if not identifier:
        return None
    return _sha256(f"{_EXTERNAL_PREFIX}{platform}:{identifier}")


def content_key(title: str | None, description: str | None) -> str | None:
    """Return the dedup key for title + substantive description, else ``None``.

    Returns ``None`` when the description is missing or shorter than
    :data:`MIN_DESCRIPTION_LENGTH` *after* normalization (so diacritics cannot inflate
    the length), or when there is no title to anchor it.
    """
    normalized_title = normalize_text(title)
    if not normalized_title:
        return None

    normalized_description = normalize_text(description)
    if len(normalized_description) < MIN_DESCRIPTION_LENGTH:
        return None

    return _sha256(f"{_CONTENT_PREFIX}{normalized_title}\n{normalized_description}")
