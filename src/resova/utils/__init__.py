"""Shared utilities: normalization, dedup identity keys, errors, retry and logging.

Implemented:
- Step 2.5: text/URL normalization (:mod:`resova.utils.normalize`) and the dedup
  identity keys (:mod:`resova.utils.identity`).

Pending (later steps): exception hierarchy, bounded backoff retry and the logging
redaction filter.
"""

from __future__ import annotations

from resova.utils.identity import (
    MIN_DESCRIPTION_LENGTH,
    content_key,
    external_key,
    url_key,
)
from resova.utils.normalize import normalize_text, normalize_url

__all__ = [
    "MIN_DESCRIPTION_LENGTH",
    "content_key",
    "external_key",
    "normalize_text",
    "normalize_url",
    "url_key",
]
