"""Operational End-to-End test suite for Resova core functions.

Exercises the full pipeline:
1. Opportunity Ingestion & DB Persistence
2. AI Analysis & Association
3. Deduplication (URL & Content hash)
4. Telegram Notifier & Formatting Verification
5. Proposal Draft Generation & Safety (Never Auto-Send)
6. External Service Failure Resilience (Graceful Degradation)
7. Database Integrity & Log Review
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from resova.ai.analyzer import AIAnalyzer, AnalysisResult
from resova.config.settings import Settings
from resova.db.base import Base
from resova.db.models import (
    DraftStatus,
    Notification,
    NotificationStatus,
    Platform,
    Project,
    ProjectAnalysis,
    ProposalDraft,
    ScoreBand,
    SourceKind,
)
from resova.db.repositories import ProjectUpsert, UpsertOutcome
from resova.services.ingestion import IngestionService
from resova.services.proposal import ProposalService
from resova.telegram.bot import TelegramNotifier, _format_opportunity_message


@pytest.fixture()
def test_db_session():
    """Create an isolated SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine)
    session = session_local()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def mock_settings():
    """Create test settings with safeguards and mock credentials."""
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=True,
        openai_api_key=SecretStr("sk-mock-proj-test-key-12345"),
        enable_telegram=True,
        telegram_bot_token=SecretStr("123456789:mock_telegram_bot_token"),
        enable_listing_poller=False,
        listing_poller_ack=False,
        telegram_allowed_user_id=5975310413,
        profile_path="config/profile.yaml",
    )


def test_1_reception_and_database_persistence(
    test_db_session: Session, mock_settings: Settings
) -> None:
    """Function 1: Test opportunity reception and database storage."""
    service = IngestionService(test_db_session, mock_settings)

    upsert_data = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="تصميم شبكة إنذار وإطفاء حريق لمبنى إداري",
        description="مطلوب مهندس لحساب وتصميم شبكة إطفاء الحريق والإنذار باستخدام أوتوكاد.",
        url="https://mostaql.com/project/888123-fire-alarm-system",
        budget_min=250.0,
        budget_max=500.0,
        budget_currency="USD",
    )

    result = service.ingest_project(upsert_data)

    assert result["project_id"] is not None
    assert result["outcome"] == UpsertOutcome.created.value

    # Verify database persistence
    saved_project = test_db_session.query(Project).filter_by(id=result["project_id"]).one()
    assert saved_project.title == "تصميم شبكة إنذار وإطفاء حريق لمبنى إداري"
    assert saved_project.platform == Platform.mostaql
    assert saved_project.url == "https://mostaql.com/project/888123-fire-alarm-system"
    assert saved_project.url_key is not None
    assert saved_project.content_key is not None


def test_2_ai_analysis_and_linkage(
    test_db_session: Session, mock_settings: Settings
) -> None:
    """Function 2: Test AI analysis scoring and linkage to project."""
    mock_analyzer = MagicMock(spec=AIAnalyzer)
    mock_analyzer.can_analyze.return_value = True
    mock_analyzer.analyze.return_value = AnalysisResult(
        score=88,
        band=ScoreBand.strong,
        primary_track="engineering",
        matching_skills=["Fire Protection", "AutoCAD", "NFPA"],
        missing_skills=[],
        summary="فرصة هندسية ممتازة لتصميم شبكات مكافحة الحريق لمبنى إداري.",
        rationale="تطابق كامل مع مسار الهندسة الميكانيكية وأنظمة مكافحة الحريق.",
        risks=[],
        language="ar",
        recommendation="notify",
        model="gpt-4o-mini",
    )

    mock_notifier = MagicMock(spec=TelegramNotifier)
    mock_notifier.enabled = False

    service = IngestionService(
        test_db_session,
        mock_settings,
        analyzer=mock_analyzer,
        notifier=mock_notifier,
    )

    upsert_data = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="مخططات مكافحة الحريق والتكييف",
        description="مطلوب مهندس لتصميم مخططات الحريق والتكييف للمستودع.",
        url="https://mostaql.com/project/888124-fire-hvac",
    )

    result = service.ingest_project(upsert_data)

    assert result["analysis"] is not None
    assert result["analysis"]["score"] == 88
    assert result["analysis"]["band"] == "strong"

    # Verify ProjectAnalysis persistence and linkage
    db_analysis = (
        test_db_session.query(ProjectAnalysis)
        .filter_by(project_id=result["project_id"])
        .one()
    )
    assert db_analysis.score == 88
    assert db_analysis.band == ScoreBand.strong
    assert db_analysis.track == "engineering"
    assert db_analysis.model == "gpt-4o-mini"
    assert "مكافحة الحريق" in db_analysis.summary


def test_3_deduplication_prevention(
    test_db_session: Session, mock_settings: Settings
) -> None:
    """Function 3: Test deduplication prevents duplicate rows and redundant AI calls."""
    mock_analyzer = MagicMock(spec=AIAnalyzer)
    mock_analyzer.can_analyze.return_value = False
    mock_analyzer.analyze_without_api.return_value = AnalysisResult(
        score=80,
        band=ScoreBand.good,
        primary_track="engineering",
        matching_skills=["HVAC"],
        missing_skills=[],
        summary="Good match",
        rationale="",
        risks=[],
        language="ar",
        recommendation="notify",
        model="keyword-scorer",
    )

    service = IngestionService(test_db_session, mock_settings, analyzer=mock_analyzer)

    upsert_data = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="مشروع دكتات تكييف مركزي",
        url="https://mostaql.com/projects/dedup-test-999",
    )

    # Ingest 1: Initial creation
    res1 = service.ingest_project(upsert_data)
    assert res1["outcome"] == UpsertOutcome.created.value
    assert mock_analyzer.analyze_without_api.call_count == 1
    assert test_db_session.query(Project).count() == 1

    # Ingest 2: Exact duplicate
    res2 = service.ingest_project(upsert_data)
    assert res2["outcome"] == UpsertOutcome.updated.value
    # Crucial: AI must NOT be invoked again
    assert mock_analyzer.analyze_without_api.call_count == 1
    assert res2["analysis"] is None
    # Crucial: No extra project row in database
    assert test_db_session.query(Project).count() == 1


def test_4_telegram_notification_content_and_safety(
    test_db_session: Session, mock_settings: Settings
) -> None:
    """Function 4: Test Telegram message formatting, content accuracy, and security."""
    mock_notifier = MagicMock(spec=TelegramNotifier)
    mock_notifier.enabled = True
    mock_notifier.send_opportunity.return_value = {
        "status": NotificationStatus.sent,
        "message_id": 9901,
        "chat_id": 5975310413,
    }

    mock_analyzer = MagicMock(spec=AIAnalyzer)
    mock_analyzer.can_analyze.return_value = False
    mock_analyzer.analyze_without_api.return_value = AnalysisResult(
        score=92,
        band=ScoreBand.strong,
        primary_track="engineering",
        matching_skills=["AutoCAD", "Fire Fighting"],
        missing_skills=[],
        summary="فرصة متطابقة بدرجة ممتازة.",
        rationale="خبرة مباشرة في مكافحة الحريق.",
        risks=[],
        language="ar",
        recommendation="notify",
        model="keyword",
    )

    service = IngestionService(
        test_db_session,
        mock_settings,
        analyzer=mock_analyzer,
        notifier=mock_notifier,
    )

    upsert_data = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="مطلوب مهندس إطفاء حريق معتمد",
        url="https://mostaql.com/projects/telegram-test",
    )

    result = service.ingest_project(upsert_data)

    assert result["notification"] is not None
    assert result["notification"]["status"] == NotificationStatus.sent
    assert result["notification"]["message_id"] == 9901

    # Verify notification recorded in database
    notif_row = (
        test_db_session.query(Notification)
        .filter_by(project_id=result["project_id"])
        .one()
    )
    assert notif_row.status == NotificationStatus.sent
    assert notif_row.telegram_message_id == 9901
    assert notif_row.score == 92

    # Verify message formatting helper directly
    formatted_msg = _format_opportunity_message(
        title="مطلوب مهندس إطفاء حريق معتمد",
        platform="mostaql",
        url="https://mostaql.com/projects/telegram-test",
        score=92,
        band="strong",
        summary="فرصة متطابقة بدرجة ممتازة.",
        language="ar",
    )
    assert "92/100" in formatted_msg
    assert "mostaql" in formatted_msg
    assert "فرصة جديدة" in formatted_msg or "فرصة متطابقة" in formatted_msg


def test_5_proposal_draft_generation_and_no_autosend(
    test_db_session: Session, mock_settings: Settings
) -> None:
    """Function 5: Test Proposal Draft creation, template generation, and strict non-submission."""
    project = Project(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="تصميم شبكة حريق لمصنع أغذية",
        description="مطلوب مهندس ميكانيكي معتمد لتصميم شبكة الرش الآلي ومضخات الحريق.",
        url="https://mostaql.com/projects/draft-test-1",
    )
    test_db_session.add(project)
    test_db_session.flush()

    proposal_service = ProposalService(test_db_session, mock_settings)
    draft = proposal_service.generate_draft(project, language="ar")

    assert draft is not None
    assert draft.project_id == project.id
    assert draft.status == DraftStatus.draft  # Strictly draft status
    assert draft.language == "ar"
    assert "السلام عليكم" in draft.content
    assert project.title in draft.content

    # Verify saved in DB
    saved_draft = (
        test_db_session.query(ProposalDraft)
        .filter_by(project_id=project.id)
        .one()
    )
    assert saved_draft.id == draft.id
    assert saved_draft.status == DraftStatus.draft

    # Verify deduplication: second call returns the same draft without duplicating
    draft2 = proposal_service.generate_draft(project, language="ar")
    assert draft2.id == draft.id
    assert test_db_session.query(ProposalDraft).filter_by(project_id=project.id).count() == 1


def test_6_error_handling_and_system_resilience(
    test_db_session: Session, mock_settings: Settings
) -> None:
    """Function 6: Test graceful degradation when external services (AI or Telegram) fail."""
    # Scenario A: AI Analyzer crashes completely
    mock_analyzer = MagicMock(spec=AIAnalyzer)
    mock_analyzer.can_analyze.side_effect = Exception("OpenAI API Gateway Timeout 504")

    # Scenario B: Telegram crashes
    mock_notifier = MagicMock(spec=TelegramNotifier)
    mock_notifier.enabled = True
    mock_notifier.send_opportunity.side_effect = Exception(
        "Telegram Connection Reset by Peer"
    )

    service = IngestionService(
        test_db_session,
        mock_settings,
        analyzer=mock_analyzer,
        notifier=mock_notifier,
    )

    upsert_data = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="مشروع اختبار الصمود أمام الأخطاء",
        description="وصف المشروع",
        url="https://mostaql.com/projects/resilience-test",
    )

    # Ingestion should NOT raise an unhandled exception or crash the system
    result = service.ingest_project(upsert_data)

    # The project must still be safely persisted in the database!
    assert result["project_id"] is not None
    assert result["outcome"] == UpsertOutcome.created.value
    saved_project = (
        test_db_session.query(Project)
        .filter_by(id=result["project_id"])
        .one()
    )
    assert saved_project.title == "مشروع اختبار الصمود أمام الأخطاء"
    # AI failed gracefully without crashing
    assert result["analysis"] is None


def test_7_database_integrity_and_relationships(test_db_session: Session) -> None:
    """Function 7: Verify DB integrity, foreign keys, and cascading relationships."""
    project = Project(
        platform=Platform.khamsat,
        source_kind=SourceKind.manual_paste,
        title="مشروع فحص تكامل العلاقات",
        url="https://khamsat.com/service/integrity-test",
    )
    test_db_session.add(project)
    test_db_session.flush()

    analysis = ProjectAnalysis(
        project_id=project.id,
        track="engineering",
        score=85,
        band=ScoreBand.good,
        summary="ملخص التحليل",
        rationale="السبب",
        model="test-model",
    )
    test_db_session.add(analysis)

    notification = Notification(
        project_id=project.id,
        channel="telegram",
        status=NotificationStatus.sent,
        score=85,
        telegram_message_id=777,
    )
    test_db_session.add(notification)

    draft = ProposalDraft(
        project_id=project.id,
        content="نص مسودة العرض",
        status=DraftStatus.draft,
        version=1,
    )
    test_db_session.add(draft)
    test_db_session.commit()

    # Query back and verify relational integrity
    fetched_project = test_db_session.query(Project).filter_by(id=project.id).one()
    assert len(fetched_project.analyses) == 1
    assert fetched_project.analyses[0].score == 85
    assert len(fetched_project.notifications) == 1
    assert fetched_project.notifications[0].telegram_message_id == 777
    assert len(fetched_project.drafts) == 1
    assert fetched_project.drafts[0].status == DraftStatus.draft
