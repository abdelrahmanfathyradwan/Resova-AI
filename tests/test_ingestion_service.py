"""Tests for the end-to-end IngestionService.

Tests the full flow: ProjectUpsert -> dedup -> scoring -> notification -> DB recording.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from resova.ai.analyzer import AIAnalyzer, AnalysisResult
from resova.config.settings import Settings
from resova.db.models import (
    Platform,
    Project,
    ProjectAnalysis,
    ScoreBand,
    SourceKind,
)
from resova.db.repositories import ProjectUpsert, UpsertOutcome
from resova.services.ingestion import IngestionService
from resova.telegram.bot import TelegramNotifier


@pytest.fixture()
def service_settings() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
    )


def test_ingest_new_project_creates_and_analyzes(
    session: Session, service_settings: Settings
) -> None:
    mock_analyzer = MagicMock(spec=AIAnalyzer)
    mock_analyzer.can_analyze.return_value = False
    mock_analyzer.analyze_without_api.return_value = AnalysisResult(
        score=82,
        band=ScoreBand.good,
        primary_track="engineering",
        matching_skills=["Fire Protection"],
        missing_skills=[],
        summary="High match engineering project.",
        rationale="Matches profile tracks.",
        risks=[],
        language="ar",
        recommendation="notify",
        model="test-scorer",
    )

    mock_notifier = MagicMock(spec=TelegramNotifier)
    mock_notifier.enabled = False

    service = IngestionService(
        session,
        service_settings,
        analyzer=mock_analyzer,
        notifier=mock_notifier,
    )

    upsert_data = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="تصميم مخططات حريق للمستودع",
        description="مطلوب مهندس متخصص في تصميم شبكات إطفاء الحريق والرش الآلي وحسابات الهيدروليك.",
        url="https://mostaql.com/projects/987654",
    )

    res = service.ingest_project(upsert_data)

    assert res["project_id"] is not None
    assert res["outcome"] == UpsertOutcome.created.value
    assert res["analysis"]["score"] == 82

    # Verify Project row in DB
    project = session.query(Project).filter_by(id=res["project_id"]).one()
    assert project.title == "تصميم مخططات حريق للمستودع"

    # Verify Analysis row in DB
    analysis = session.query(ProjectAnalysis).filter_by(project_id=project.id).one()
    assert analysis.score == 82
    assert analysis.track == "engineering"


def test_ingest_duplicate_project_does_not_reanalyze(
    session: Session, service_settings: Settings
) -> None:
    mock_analyzer = MagicMock(spec=AIAnalyzer)
    mock_analyzer.can_analyze.return_value = False
    mock_analyzer.analyze_without_api.return_value = AnalysisResult(
        score=75,
        band=ScoreBand.good,
        primary_track="engineering",
        matching_skills=["HVAC"],
        missing_skills=[],
        summary="Good match",
        rationale="",
        risks=[],
        language="ar",
        recommendation="notify",
        model="test",
    )

    service = IngestionService(session, service_settings, analyzer=mock_analyzer)

    upsert_data = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="تصميم دكتات تكييف",
        url="https://mostaql.com/projects/555",
    )

    # First ingest
    res1 = service.ingest_project(upsert_data)
    assert res1["outcome"] == UpsertOutcome.created.value
    assert mock_analyzer.analyze_without_api.call_count == 1

    # Second ingest with same URL
    res2 = service.ingest_project(upsert_data)
    assert res2["outcome"] == UpsertOutcome.updated.value
    # Analysis must NOT be triggered again for existing project
    assert mock_analyzer.analyze_without_api.call_count == 1


def test_manual_paste_helper(session: Session, service_settings: Settings) -> None:
    service = IngestionService(session, service_settings)
    res = service.ingest_manual_paste(
        title="إعداد عرض بوربوينت احترافي",
        description="تصميم بروفايل شركة مكون من 20 شريحة بالبوربوينت.",
    )
    assert res["project_id"] is not None
    assert res["outcome"] == UpsertOutcome.created.value
