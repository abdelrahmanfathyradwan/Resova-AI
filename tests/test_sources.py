"""Tests for the source contract (Phase 1 / Step 2.7).

No concrete Mostaql/Khamsat source exists yet, so these tests cover the interface itself:
the status labels, the capability descriptor, the raw project container, the unified
model, and the guarantee that the module imports no networking client.
"""

from __future__ import annotations

import ast
from datetime import datetime
from pathlib import Path

import pytest

from resova.db.models import Platform, SourceKind
from resova.db.repositories import ProjectUpsert
from resova.sources import (
    BaseSource,
    RawProject,
    SourceCapabilities,
    SourceStatus,
    UnifiedProject,
)

MODULE_PATH = Path(__file__).resolve().parents[1] / "src" / "resova" / "sources" / "base.py"

#: Networking clients a source module must never import. ``BaseSource`` describes a
#: contract; it must not be able to reach the network at all.
FORBIDDEN_IMPORTS = {
    "socket",
    "ssl",
    "http",
    "http.client",
    "requests",
    "httpx",
    "aiohttp",
    "urllib.request",
    "urllib3",
}


class _FakeSource(BaseSource):
    """Minimal concrete source used to prove the interface is implementable."""

    @property
    def platform(self) -> Platform:
        return Platform.mostaql

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.not_implemented_pending_samples

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            platform=Platform.mostaql,
            supported_kinds=frozenset({SourceKind.alert_email}),
        )

    def fetch_new(self, since: datetime | None = None) -> list[RawProject]:
        return [RawProject(source_kind=SourceKind.alert_email, title="Fix a bug")]

    def normalize(self, raw: RawProject) -> UnifiedProject | None:
        if not raw.title:
            return None
        return UnifiedProject(
            platform=self.platform,
            source_kind=raw.source_kind,
            title=raw.title,
        )

    def health_check(self) -> bool:
        return True


class TestSourceStatus:
    def test_labels_are_exact(self) -> None:
        assert SourceStatus.implemented == "Implemented"
        assert (
            SourceStatus.not_implemented_pending_samples
            == "Not implemented - pending sample emails"
        )

    def test_only_two_statuses_exist(self) -> None:
        assert {member.name for member in SourceStatus} == {
            "implemented",
            "not_implemented_pending_samples",
        }


class TestSourceCapabilities:
    def test_supports_returns_true_for_declared_kind(self) -> None:
        capabilities = SourceCapabilities(
            platform=Platform.khamsat,
            supported_kinds=frozenset({SourceKind.alert_email, SourceKind.manual_paste}),
        )
        assert capabilities.supports(SourceKind.alert_email) is True
        assert capabilities.supports(SourceKind.manual_paste) is True

    def test_supports_returns_false_for_undeclared_kind(self) -> None:
        capabilities = SourceCapabilities(
            platform=Platform.khamsat,
            supported_kinds=frozenset({SourceKind.alert_email}),
        )
        assert capabilities.supports(SourceKind.manual_paste) is False

    def test_empty_capabilities_support_nothing(self) -> None:
        capabilities = SourceCapabilities(platform=Platform.manual, supported_kinds=frozenset())
        assert all(not capabilities.supports(kind) for kind in SourceKind)

    def test_is_immutable(self) -> None:
        capabilities = SourceCapabilities(
            platform=Platform.mostaql,
            supported_kinds=frozenset({SourceKind.alert_email}),
        )
        with pytest.raises(AttributeError):
            capabilities.platform = Platform.khamsat  # type: ignore[misc]


class TestRawProject:
    def test_only_source_kind_is_required(self) -> None:
        raw = RawProject(source_kind=SourceKind.manual_paste)
        assert raw.title is None
        assert raw.description is None
        assert raw.url is None
        assert raw.external_id is None
        assert raw.published_at is None
        assert raw.extras == {}

    def test_extras_default_is_not_shared_between_instances(self) -> None:
        first = RawProject(source_kind=SourceKind.alert_email)
        second = RawProject(source_kind=SourceKind.alert_email)
        assert first.extras is not second.extras

    def test_is_immutable(self) -> None:
        raw = RawProject(source_kind=SourceKind.alert_email, title="Logo")
        with pytest.raises(AttributeError):
            raw.title = "Other"  # type: ignore[misc]


class TestUnifiedProject:
    def test_is_the_repository_input_type(self) -> None:
        assert UnifiedProject is ProjectUpsert


class TestBaseSource:
    def test_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            BaseSource()  # type: ignore[abstract]

    def test_declares_every_abstract_member(self) -> None:
        assert BaseSource.__abstractmethods__ == frozenset(
            {"platform", "status", "capabilities", "fetch_new", "normalize", "health_check"}
        )

    def test_partial_implementation_is_still_abstract(self) -> None:
        class Incomplete(BaseSource):
            @property
            def platform(self) -> Platform:
                return Platform.manual

            @property
            def status(self) -> SourceStatus:
                return SourceStatus.not_implemented_pending_samples

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_concrete_source_satisfies_the_contract(self) -> None:
        source = _FakeSource()
        assert source.platform is Platform.mostaql
        assert source.status is SourceStatus.not_implemented_pending_samples
        assert source.capabilities().supports(SourceKind.alert_email) is True
        assert source.health_check() is True

    def test_concrete_source_normalizes_a_raw_item(self) -> None:
        source = _FakeSource()
        raw = RawProject(source_kind=SourceKind.alert_email, title="Fix a bug")
        unified = source.normalize(raw)
        assert unified is not None
        assert unified.platform is Platform.mostaql
        assert unified.title == "Fix a bug"

    def test_concrete_source_rejects_an_unusable_item(self) -> None:
        source = _FakeSource()
        assert source.normalize(RawProject(source_kind=SourceKind.alert_email)) is None


class TestCompliance:
    def test_module_imports_no_networking_client(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        assert imported & FORBIDDEN_IMPORTS == set()
