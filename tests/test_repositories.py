"""Tests for the project repository and its idempotent upsert (Phase 1 / Step 2.5).

These are the approval-mandated dedup cases: key backfill, conflicting keys, generic
titles with different descriptions, Arabic variants, percent-encoded URLs and the
unique-key race path.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from resova.db.base import utcnow
from resova.db.models import Platform, Project, SourceKind
from resova.db.repositories import (
    LatencyStats,
    ProjectRepository,
    ProjectUpsert,
    UpsertOutcome,
)

LONG_EN = "We need a complete professional presentation for an internal training course."
LONG_AR = "مطلوب تصميم عرض تقديمي احترافي كامل لدورة تدريبية داخلية للشركة."
LONG_AR_DIACRITICS = "مَطلوب تَصميم عَرض تَقديمي احترافي كامل لدورة تدريبية داخِلية للشركة."


def make(**overrides: object) -> ProjectUpsert:
    """Build a :class:`ProjectUpsert`, overriding only what a test cares about."""
    fields: dict[str, object] = {
        "platform": Platform.mostaql,
        "source_kind": SourceKind.manual_paste,
        "title": "generic title",
    }
    fields.update(overrides)
    return ProjectUpsert(**fields)  # type: ignore[arg-type]


def count_projects(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Project)) or 0


@pytest.fixture()
def repo(session: Session) -> ProjectRepository:
    return ProjectRepository(session)


class TestIdempotency:
    def test_insert_then_same_input_updates_a_single_row(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        first = repo.upsert_project(make(url="https://mostaql.com/p/1", external_id="1"))
        assert first.outcome is UpsertOutcome.created
        session.commit()

        second = repo.upsert_project(make(url="https://mostaql.com/p/1", external_id="1"))
        assert second.outcome is UpsertOutcome.updated
        assert second.project.id == first.project.id
        assert count_projects(session) == 1

    def test_no_keys_means_every_call_inserts(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        # No URL, no external id, no substantive description -> no key -> no dedup.
        repo.upsert_project(make(title="professional CV design"))
        repo.upsert_project(make(title="professional CV design"))
        assert count_projects(session) == 2

    def test_last_seen_at_advances_on_reupsert(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        first = repo.upsert_project(make(title="task", url="https://example.com/t"))
        seen = first.project.last_seen_at
        session.commit()

        second = repo.upsert_project(make(title="task", url="https://example.com/t"))
        assert second.project.last_seen_at >= seen

    def test_normalized_url_is_persisted(self, repo: ProjectRepository, session: Session) -> None:
        result = repo.upsert_project(make(title="task", url="https://example.com/%D8%AA#frag"))
        assert result.project.normalized_url == "https://example.com/ت"


class TestBackfill:
    def test_manual_paste_then_email_backfills_keys(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        manual = repo.upsert_project(
            make(source_kind=SourceKind.manual_paste, title="design task", description=LONG_EN)
        )
        assert manual.outcome is UpsertOutcome.created
        row = manual.project
        assert row.url_key is None
        assert row.external_key is None
        assert row.content_key is not None
        session.commit()

        email = repo.upsert_project(
            make(
                source_kind=SourceKind.alert_email,
                title="design task",
                description=LONG_EN,
                url="https://mostaql.com/p/9",
                external_id="9",
            )
        )
        assert email.outcome is UpsertOutcome.updated
        assert email.project.id == row.id
        assert email.project.url == "https://mostaql.com/p/9"
        assert email.project.url_key is not None
        assert email.project.external_key is not None
        assert count_projects(session) == 1

    def test_email_then_manual_paste_backfills_content_key(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        email = repo.upsert_project(
            make(
                source_kind=SourceKind.alert_email,
                title="design task",
                url="https://mostaql.com/p/5",
            )
        )
        assert email.project.content_key is None
        session.commit()

        manual = repo.upsert_project(
            make(
                source_kind=SourceKind.manual_paste,
                title="design task",
                url="https://mostaql.com/p/5",
                description=LONG_EN,
            )
        )
        assert manual.outcome is UpsertOutcome.updated
        assert manual.project.id == email.project.id
        assert manual.project.content_key is not None
        assert count_projects(session) == 1

    def test_existing_keys_are_write_once(self, repo: ProjectRepository, session: Session) -> None:
        first = repo.upsert_project(
            make(title="task", external_id="E", url="https://mostaql.com/p/1")
        )
        session.commit()

        second = repo.upsert_project(
            make(title="task", external_id="E", url="https://mostaql.com/p/2")
        )
        assert second.project.id == first.project.id
        # The first URL wins: an established key is never overwritten.
        assert second.project.url == "https://mostaql.com/p/1"
        assert second.project.url_key == first.project.url_key


class TestMatching:
    def test_same_url_via_email_and_manual_is_one_row(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        a = repo.upsert_project(
            make(source_kind=SourceKind.alert_email, title="t1", url="https://mostaql.com/p/42")
        )
        b = repo.upsert_project(
            make(source_kind=SourceKind.manual_paste, title="t1", url="https://mostaql.com/p/42")
        )
        assert a.project.id == b.project.id
        assert count_projects(session) == 1

    def test_same_external_id_with_different_urls_is_one_row(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        a = repo.upsert_project(make(title="t", external_id="E1", url="https://mostaql.com/p/1"))
        b = repo.upsert_project(make(title="t", external_id="E1", url="https://mostaql.com/p/2"))
        assert a.project.id == b.project.id
        assert count_projects(session) == 1

    def test_same_generic_title_different_descriptions_stay_separate(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        repo.upsert_project(make(title="professional CV design", description=LONG_EN))
        repo.upsert_project(
            make(
                title="professional CV design",
                description="We need a brand new visual identity for a small bakery in Cairo.",
            )
        )
        assert count_projects(session) == 2

    def test_same_title_and_description_dedupe(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        a = repo.upsert_project(make(title="design task", description=LONG_EN))
        b = repo.upsert_project(make(title="design task", description=LONG_EN))
        assert a.project.id == b.project.id
        assert count_projects(session) == 1

    def test_url_less_manual_duplicates_dedupe_by_content(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        a = repo.upsert_project(make(title="task", description=LONG_AR))
        b = repo.upsert_project(make(title="task", description=LONG_AR))
        assert a.project.id == b.project.id
        assert count_projects(session) == 1

    def test_arabic_variants_dedupe(self, repo: ProjectRepository, session: Session) -> None:
        a = repo.upsert_project(make(title="تصميم", description=LONG_AR))
        b = repo.upsert_project(make(title="تَصميم", description=LONG_AR_DIACRITICS))
        assert a.project.id == b.project.id
        assert count_projects(session) == 1

    def test_percent_encoded_and_decoded_urls_are_the_same_project(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        a = repo.upsert_project(
            make(title="t", url="https://mostaql.com/project/%D8%AA%D8%B5%D9%85%D9%8A%D9%85")
        )
        b = repo.upsert_project(make(title="t", url="https://mostaql.com/project/تصميم"))
        assert a.project.id == b.project.id
        assert count_projects(session) == 1


class TestConflicts:
    def test_conflicting_keys_keep_earliest_without_crashing(
        self, repo: ProjectRepository, session: Session, caplog: pytest.LogCaptureFixture
    ) -> None:
        first = repo.upsert_project(make(title="project a", url="https://mostaql.com/p/1"))
        second = repo.upsert_project(
            make(title="project b", external_id="E2", url="https://mostaql.com/p/2")
        )
        assert first.project.id != second.project.id
        session.commit()

        with caplog.at_level(logging.WARNING):
            conflict = repo.upsert_project(
                make(title="merged", url="https://mostaql.com/p/1", external_id="E2")
            )

        assert conflict.outcome is UpsertOutcome.conflict
        assert conflict.project.id == first.project.id  # earliest row kept
        assert set(conflict.matched_row_ids) == {first.project.id, second.project.id}
        assert count_projects(session) == 2  # no new row and no UNIQUE violation
        assert any("conflicting dedup keys" in record.message for record in caplog.records)

    def test_integrity_error_race_is_recovered(
        self,
        repo: ProjectRepository,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        existing = repo.upsert_project(make(title="raced", url="https://mostaql.com/p/7"))
        assert existing.outcome is UpsertOutcome.created
        session.commit()

        real_find = repo.find_by_keys
        calls = {"n": 0}

        def flaky_find(keys: dict[str, str | None]) -> list[Project]:
            calls["n"] += 1
            if calls["n"] == 1:
                return []  # pretend we missed the committed row -> forces the INSERT
            return real_find(keys)

        monkeypatch.setattr(repo, "find_by_keys", flaky_find)

        with caplog.at_level(logging.WARNING):
            result = repo.upsert_project(
                make(title="raced", url="https://mostaql.com/p/7", external_id="7")
            )

        assert calls["n"] >= 2  # re-read inside the transaction happened
        assert result.outcome is UpsertOutcome.updated
        assert result.project.id == existing.project.id
        assert result.project.external_key is not None  # backfilled after recovery
        assert count_projects(session) == 1
        assert any("dedup race recovered" in record.message for record in caplog.records)

    def test_integrity_error_without_existing_row_reraises(
        self, repo: ProjectRepository, session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        repo.upsert_project(make(title="raced", url="https://mostaql.com/p/8"))
        session.commit()

        # The re-read finds nothing -> the original IntegrityError must surface.
        monkeypatch.setattr(repo, "find_by_keys", lambda _keys: [])
        with pytest.raises(IntegrityError):
            repo.upsert_project(make(title="raced", url="https://mostaql.com/p/8"))

    def test_integrity_error_then_conflict_is_recorded(
        self,
        repo: ProjectRepository,
        session: Session,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        repo.upsert_project(make(title="project a", url="https://mostaql.com/p/1"))
        repo.upsert_project(
            make(title="project b", external_id="E2", url="https://mostaql.com/p/2")
        )
        session.commit()

        real_find = repo.find_by_keys
        calls = {"n": 0}

        def flaky_find(keys: dict[str, str | None]) -> list[Project]:
            calls["n"] += 1
            if calls["n"] == 1:
                return []  # force the INSERT, which then collides on url_key
            return real_find(keys)

        monkeypatch.setattr(repo, "find_by_keys", flaky_find)

        with caplog.at_level(logging.WARNING):
            result = repo.upsert_project(
                make(title="merged", url="https://mostaql.com/p/1", external_id="E2")
            )

        assert result.outcome is UpsertOutcome.conflict
        assert count_projects(session) == 2
        assert any("conflicting dedup keys" in record.message for record in caplog.records)


class TestDiscoveryLatency:
    def test_empty_when_no_publish_dates(self, repo: ProjectRepository, session: Session) -> None:
        repo.upsert_project(make(title="t", url="https://example.com/t"))
        session.commit()
        assert repo.discovery_latency() == LatencyStats()

    def test_statistics_over_known_deltas(self, repo: ProjectRepository, session: Session) -> None:
        repo.upsert_project(
            make(title="a", url="https://example.com/a", published_at=utcnow() - timedelta(hours=2))
        )
        repo.upsert_project(
            make(title="b", url="https://example.com/b", published_at=utcnow() - timedelta(hours=1))
        )
        repo.upsert_project(make(title="c", url="https://example.com/c"))  # no publish date
        session.commit()

        stats = repo.discovery_latency()
        assert stats.count == 2
        assert stats.minimum_seconds is not None and 3500 <= stats.minimum_seconds <= 3700
        assert stats.maximum_seconds is not None and 7100 <= stats.maximum_seconds <= 7300
        assert stats.average_seconds is not None and 5300 <= stats.average_seconds <= 5500

    def test_future_publish_dates_are_ignored(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        repo.upsert_project(
            make(title="f", url="https://example.com/f", published_at=utcnow() + timedelta(hours=1))
        )
        session.commit()
        assert repo.discovery_latency().count == 0

    def test_filters_by_platform(self, repo: ProjectRepository, session: Session) -> None:
        repo.upsert_project(
            make(
                platform=Platform.mostaql,
                title="a",
                url="https://mostaql.com/a",
                published_at=utcnow() - timedelta(hours=2),
            )
        )
        repo.upsert_project(
            make(
                platform=Platform.khamsat,
                title="b",
                url="https://khamsat.com/b",
                published_at=utcnow() - timedelta(hours=3),
            )
        )
        session.commit()

        stats = repo.discovery_latency(platform=Platform.khamsat)
        assert stats.count == 1
        assert stats.average_seconds is not None and stats.average_seconds >= 3 * 3600 - 60

    def test_since_filter_excludes_older_projects(
        self, repo: ProjectRepository, session: Session
    ) -> None:
        repo.upsert_project(
            make(
                title="old",
                url="https://example.com/old",
                published_at=utcnow() - timedelta(days=10),
            )
        )
        repo.upsert_project(
            make(
                title="new",
                url="https://example.com/new",
                published_at=utcnow() - timedelta(hours=1),
            )
        )
        session.commit()

        stats = repo.discovery_latency(since=utcnow() - timedelta(days=1))
        assert stats.count == 1
