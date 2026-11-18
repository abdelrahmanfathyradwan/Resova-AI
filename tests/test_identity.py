"""Tests for the dedup identity keys (Phase 1 / Step 2.5).

These tests encode the approved rules: keys are stable, normalizing, and never built
from a title alone.
"""

from __future__ import annotations

from resova.utils.identity import MIN_DESCRIPTION_LENGTH, content_key, external_key, url_key

_LONG_EN = "We need a complete professional presentation for an internal training course."
_LONG_AR = "مطلوب تصميم عرض تقديمي احترافي كامل لدورة تدريبية داخلية للشركة."
_SHORT = "تصميم عرض"


class TestUrlKey:
    def test_none_or_blank_yields_none(self) -> None:
        assert url_key(None) is None
        assert url_key("") is None
        assert url_key("   ") is None

    def test_is_64_char_hex(self) -> None:
        key = url_key("https://mostaql.com/project/1")
        assert key is not None
        assert len(key) == 64
        int(key, 16)  # raises ValueError if not valid hex

    def test_percent_encoded_and_decoded_agree(self) -> None:
        encoded = "https://mostaql.com/project/%D8%AA%D8%B5%D9%85%D9%8A%D9%85"
        decoded = "https://mostaql.com/project/تصميم"
        assert url_key(encoded) == url_key(decoded)

    def test_fragment_is_ignored(self) -> None:
        assert url_key("https://example.com/p#a") == url_key("https://example.com/p#b")

    def test_different_urls_differ(self) -> None:
        assert url_key("https://example.com/a") != url_key("https://example.com/b")


class TestExternalKey:
    def test_none_or_blank_yields_none(self) -> None:
        assert external_key("mostaql", None) is None
        assert external_key("mostaql", "   ") is None

    def test_stable_for_same_input(self) -> None:
        assert external_key("mostaql", "ABC-123") == external_key("mostaql", "abc-123")

    def test_platform_is_part_of_the_key(self) -> None:
        assert external_key("mostaql", "1") != external_key("khamsat", "1")


class TestContentKey:
    def test_missing_or_short_description_yields_none(self) -> None:
        assert content_key("professional CV design", None) is None
        assert content_key("professional CV design", _SHORT) is None
        assert len(_SHORT) < MIN_DESCRIPTION_LENGTH

    def test_missing_title_yields_none(self) -> None:
        assert content_key(None, _LONG_EN) is None
        assert content_key("", _LONG_EN) is None

    def test_same_generic_title_different_descriptions_stay_separate(self) -> None:
        first = content_key("professional CV design", _LONG_EN)
        second = content_key(
            "professional CV design",
            "We need a brand new visual identity for a small bakery in Cairo.",
        )
        assert first is not None and second is not None
        assert first != second

    def test_identical_title_and_description_match(self) -> None:
        assert content_key("design task", _LONG_EN) == content_key("design task", _LONG_EN)

    def test_arabic_variants_normalize_to_the_same_key(self) -> None:
        with_tashkeel = "مَطلوب تَصميم عَرض تَقديمي احترافي كامل لدورة تدريبية داخِلية للشركة."
        assert content_key("تصميم", _LONG_AR) == content_key("تصميم", with_tashkeel)

    def test_whitespace_and_case_do_not_matter(self) -> None:
        assert content_key("Design Task", _LONG_EN) == content_key(
            "  design   task  ", f"  {_LONG_EN}  "
        )

    def test_title_only_never_matches(self) -> None:
        # Two rows with the same title but no usable description must not dedup.
        assert content_key("professional CV design", None) is None
        assert content_key("professional CV design", None) is None
