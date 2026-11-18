"""Text and URL normalization used to compute dedup keys (Phase 1 / Step 2.5).

Normalization exists for exactly one purpose: making two strings that describe the *same*
thing compare equal, so duplicates are caught. It never guesses or invents data, and it
never performs a network request.

Rules (agreed decision):

* Unicode NFKC, Arabic tashkeel/tatweel removed, alef/ya/ta-marbuta variants unified,
  whitespace collapsed and Latin lower-cased.
* URLs are percent-decoded (so ``%D8%A3`` and ``أ`` are the same), the scheme/host are
  lower-cased and the fragment is dropped. Platform-specific id extraction is **not**
  attempted here - it waits for real sample emails in Phase 3 (``TODO(unverified)``).
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit

# Arabic combining marks: tashkeel (harakat), superscript alef and Quranic marks.
_TASHKEEL = re.compile("[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
_TATWEEL = "\u0640"
_WHITESPACE = re.compile(r"\s+")

# Variant letters folded to their canonical form.
_ALEF_VARIANTS = {
    "\u0622": "\u0627",  # آ -> ا
    "\u0623": "\u0627",  # أ -> ا
    "\u0625": "\u0627",  # إ -> ا
    "\u0671": "\u0627",  # ٱ -> ا
}
_YA_VARIANTS = {"\u0649": "\u064a"}  # ى -> ي
_TA_MARBUTA_VARIANTS = {"\u0629": "\u0647"}  # ة -> ه

_DEFAULT_PORTS = {"http": ":80", "https": ":443"}


def normalize_text(value: str | None) -> str:
    """Return a canonical form of ``value`` suitable for equality comparison."""
    if not value:
        return ""

    text = unicodedata.normalize("NFKC", value)
    text = text.replace(_TATWEEL, "")
    text = _TASHKEEL.sub("", text)
    text = _translate(text, _ALEF_VARIANTS)
    text = _translate(text, _YA_VARIANTS)
    text = _translate(text, _TA_MARBUTA_VARIANTS)
    text = text.lower()
    return _WHITESPACE.sub(" ", text).strip()


def normalize_url(value: str | None) -> str | None:
    """Return a canonical, percent-decoded form of ``value``.

    Returns ``None`` for a missing or blank URL. Never fetches anything, and never tries
    to interpret a platform id inside the URL.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None

    split = urlsplit(raw)
    scheme = split.scheme.strip().lower()
    netloc = split.netloc.strip().lower()

    default_port = _DEFAULT_PORTS.get(scheme)
    if default_port and netloc.endswith(default_port):
        netloc = netloc[: -len(default_port)]

    path = unquote(split.path)
    if path == "/":
        path = ""
    query = unquote(split.query)

    # The fragment (``#...``) is client-side state, never part of a project's identity.
    normalized = urlunsplit((scheme, netloc, path, query, ""))
    normalized = unicodedata.normalize("NFKC", normalized).strip()
    return normalized or None


def _translate(text: str, mapping: dict[str, str]) -> str:
    return "".join(mapping.get(char, char) for char in text)
