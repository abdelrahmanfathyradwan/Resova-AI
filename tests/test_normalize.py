"""Tests for text/URL normalization (Phase 1 / Step 2.5).

The rules here are the foundation of dedup, so they are tested directly. The normalizer
is also proven to be offline: it must never open a socket.
"""

from __future__ import annotations

import socket

import pytest

from resova.utils.normalize import normalize_text, normalize_url


class TestNormalizeText:
    def test_returns_empty_for_missing_values(self) -> None:
        assert normalize_text(None) == ""
        assert normalize_text("") == ""

    def test_collapses_whitespace_and_lowercases_latin(self) -> None:
        assert normalize_text("  Hello \t World\n ") == "hello world"

    def test_strips_tashkeel(self) -> None:
        # مُحَمَّد (with harakat) folds to محمد.
        assert (
            normalize_text("\u0645\u064f\u062d\u064e\u0645\u064e\u0651\u062f")
            == "\u0645\u062d\u0645\u062f"
        )

    def test_strips_tatweel(self) -> None:
        assert normalize_text("مـــوقع") == normalize_text("موقع")

    @pytest.mark.parametrize(
        "variant",
        [
            "\u0623\u062d\u0645\u062f",
            "\u0625\u062d\u0645\u062f",
            "\u0622\u062d\u0645\u062f",
            "\u0671\u062d\u0645\u062f",
        ],
    )
    def test_folds_alef_variants(self, variant: str) -> None:
        assert normalize_text(variant) == normalize_text("\u0627\u062d\u0645\u062f")

    def test_folds_alef_maqsura_to_ya(self) -> None:
        assert normalize_text("مصطف\u0649") == normalize_text("مصطفي")

    def test_folds_ta_marbuta_to_ha(self) -> None:
        assert normalize_text("خدم\u0629") == normalize_text("خدم\u0647")

    def test_nfkc_compatibility_form(self) -> None:
        # Full-width Latin letters fold to plain ASCII under NFKC.
        assert normalize_text("\uff28\uff45\uff4c\uff4c\uff4f") == "hello"


class TestNormalizeUrl:
    def test_returns_none_for_missing_values(self) -> None:
        assert normalize_url(None) is None
        assert normalize_url("") is None
        assert normalize_url("   ") is None

    def test_percent_encoded_and_decoded_agree(self) -> None:
        encoded = "https://mostaql.com/project/%D8%AA%D8%B5%D9%85%D9%8A%D9%85"
        decoded = "https://mostaql.com/project/تصميم"
        assert normalize_url(encoded) == normalize_url(decoded)

    def test_drops_fragment(self) -> None:
        assert normalize_url("https://example.com/p#section") == "https://example.com/p"

    def test_lowercases_scheme_and_host_only(self) -> None:
        normalized = normalize_url("HTTPS://Example.COM/CaseSensitive")
        assert normalized == "https://example.com/CaseSensitive"

    def test_drops_default_port(self) -> None:
        assert normalize_url("https://example.com:443/p") == "https://example.com/p"

    def test_root_path_is_trimmed(self) -> None:
        assert normalize_url("https://example.com/") == "https://example.com"

    def test_never_opens_a_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("normalize_url must never perform a network request")

        monkeypatch.setattr(socket, "socket", _boom)
        monkeypatch.setattr(socket, "create_connection", _boom)
        assert normalize_url("https://mostaql.com/project/1") == "https://mostaql.com/project/1"
