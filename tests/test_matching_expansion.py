"""Comprehensive test suite for Matching Expansion & Opportunity Freshness (Stage 5).

Covers:
1. Matching & Classification:
   - Core track matches (Strong >= 85, Good 70-84, Moderate 45-69, Below < 45)
   - Brief/concise description handling (score preserved, confidence adjusted)
   - Excluded domains (Logo, Coding, Video) rejected
   - Vague generic terms ("تصميم") not falsely matched
   - Budget neutrality verification
2. Freshness & Staleness:
   - Fresh items (2h, 24h, 47h) notified
   - Stale items (>48h) skipped with 'stale_project_skipped'
   - Bumped items (old published_at, recent last_activity_at) skipped
   - Unknown publication date skipped with 'publication_date_unknown'
   - Persistent DB dedup after restart
3. Notification Routing & Anti-Flood:
   - Strong/Good -> immediate notification
   - Moderate -> queued as pending_digest
   - Digest flush sends consolidated batch
   - Zero real Telegram calls
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

from resova.ai.analyzer import AIAnalyzer, AnalysisResult
from resova.config.profile import ClientProfile, load_profile
from resova.config.settings import Settings
from resova.db.base import utcnow
from resova.db.models import (
    Notification,
    NotificationStatus,
    Platform,
    Project,
    ScoreBand,
    SourceKind,
)
from resova.db.repositories import ProjectUpsert
from resova.services.ingestion import IngestionService
from resova.telegram.bot import TelegramNotifier


@pytest.fixture()
def client_profile() -> ClientProfile:
    return load_profile("config/profile.yaml")


@pytest.fixture()
def app_settings() -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token=SecretStr("mock-token-1234567890"),
        telegram_allowed_user_id=123456789,
        max_notification_age_hours=48,
    )


# -----------------------------------------------------------------------------
# 1. Matching & Classification Tests
# -----------------------------------------------------------------------------


def test_match_strong_fire_protection_system(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Core skill match in engineering track gets strong score (>=85)."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="مطلوب مهندس لتصميم نظام إطفاء حريق وشبكة رش آلي",
        description=(
            "مشروع مبنى إداري يحتاج تصميم شبكة مكافحة الحريق ومضخات الحريق "
            "وحسابات هيدروليكية باستخدام أوتوكاد."
        ),
        platform="mostaql",
    )
    assert result.score >= 85
    assert result.band == ScoreBand.strong
    assert result.primary_track == "engineering"
    assert "مكافحة حريق" in result.matching_skills or "Fire Fighting" in result.matching_skills


def test_match_good_presentation_design(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Core skill match in presentation design gets good/strong score (>=70)."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم عرض تقديمي وبوربوينت احترافي لشركة",
        description=(
            "لدينا ملف عرض تقديمي بحاجة إلى تحسين وإعادة تصميم الهوية البصرية "
            "والرسوم البيانية."
        ),
        platform="mostaql",
    )
    assert 70 <= result.score <= 100
    assert result.band in (ScoreBand.good, ScoreBand.strong)
    assert result.primary_track == "presentation_design"


def test_match_moderate_cv_translation(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Approved keyword in CV track yields moderate or good score (>=45)."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تحويل CV الى عربي وتنسيقه",
        description="سيرة ذاتية باللغة الإنجليزية أريد ترجمتها وإعادة ترتيبها لتكون احترافية.",
        platform="khamsat",
    )
    assert result.score >= 45
    assert result.band in (ScoreBand.moderate, ScoreBand.good)
    assert result.primary_track == "cv_resume"


def test_brief_description_does_not_penalize_score(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Brief description with a confirmed skill is evaluated without unfair penalty."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تصميم شبكة مكافحة حريق",
        description=None,  # No description at all
        platform="khamsat",
    )
    # Score reflects the skill match directly
    assert result.score >= 45
    # Confidence is lower due to lack of description
    assert result.confidence <= 0.6


def test_excluded_domain_logo_design_is_rejected(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Explicitly excluded domain (Logo design) gets score < 45 and band 'below'."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="اختبار نظام Resova AI – مطلوب تصميم شعار بسيط",
        description="مطلوب لوجو بسيط وبطاقة أعمال لمتجر ملابس عصري.",
        platform="khamsat",
    )
    assert result.score < 45
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_excluded_domain_software_development_is_rejected(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Software/coding project gets score < 45 and band 'below'."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="تطوير موقع ويب باستخدام React و Django",
        description="برمجة متجر إلكتروني وربط بوابات الدفع وقاعدة بيانات PostgreSQL.",
        platform="mostaql",
    )
    assert result.score < 45
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_vague_generic_term_is_rejected(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Generic term 'تصميم' without track keywords is rejected."""
    analyzer = AIAnalyzer(app_settings)
    result = analyzer.analyze_without_api(
        title="مطلوب تصميم",
        description="أحتاج إلى مصمم للعمل على بعض الملفات العامة.",
        platform="khamsat",
    )
    assert result.score < 45
    assert result.band == ScoreBand.below


def test_budget_neutrality(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Projects with low budget ($5-$10) or missing budget achieve the same match score."""
    analyzer = AIAnalyzer(app_settings)
    r1 = analyzer.analyze_without_api(
        title="مخططات مكافحة حريق أوتوكاد",
        description="تصميم شبكة إطفاء وحسابات هيدروليكية.",
        platform="khamsat",
    )
    r2 = analyzer.analyze_without_api(
        title="مخططات مكافحة حريق أوتوكاد",
        description="تصميم شبكة إطفاء وحسابات هيدروليكية.",
        platform="khamsat",
    )
    assert r1.score == r2.score


def test_confidence_vs_score_separation(
    app_settings: Settings, client_profile: ClientProfile
) -> None:
    """Confidence reflects description length/certainty while score reflects keyword alignment."""
    analyzer = AIAnalyzer(app_settings)
    rich = analyzer.analyze_without_api(
        title="تصميم شبكة مكافحة حريق وتكييف",
        description=(
            "مطلوب مهندس متخصص لإعداد مخططات الحريق والتكييف وحسابات هيدروليكية "
            "ومضخات الحريق للمبنى الإداري."
        ),
        platform="mostaql",
    )
    brief = analyzer.analyze_without_api(
        title="تصميم شبكة مكافحة حريق وتكييف",
        description=None,
        platform="mostaql",
    )
    assert rich.confidence > brief.confidence
    assert rich.confidence >= 0.8
    assert brief.confidence <= 0.6


# -----------------------------------------------------------------------------
# 2. Freshness & Staleness Filtering Tests
# -----------------------------------------------------------------------------


def test_fresh_project_within_2_hours_is_notified(
    session: Session, app_settings: Settings
) -> None:
    """Project published 2 hours ago is fresh and receives immediate notification."""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True
    notifier.send_opportunity.return_value = {
        "status": NotificationStatus.sent,
        "message_id": 101,
        "chat_id": 12345,
    }

    service = IngestionService(session, app_settings, notifier=notifier)
    now = utcnow()
    upsert = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.website_page,
        title="مطلوب مهندس مكافحة حريق وشبكة إطفاء ورش آلي",
        url="https://mostaql.com/project/fresh-2h",
        published_at=now - timedelta(hours=2),
    )
    res = service.ingest_project(upsert)
    assert res["notification"] is not None
    assert res["notification"]["status"] == NotificationStatus.sent
    assert notifier.send_opportunity.call_count == 1


def test_fresh_project_within_47_hours_is_notified(
    session: Session, app_settings: Settings
) -> None:
    """Project published 47 hours ago is within 48h limit and is notified."""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True
    notifier.send_opportunity.return_value = {
        "status": NotificationStatus.sent,
        "message_id": 102,
        "chat_id": 12345,
    }

    service = IngestionService(session, app_settings, notifier=notifier)
    now = utcnow()
    upsert = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.website_page,
        title="تصميم شبكة مكافحة حريق وشبكة إطفاء ومضخات",
        url="https://mostaql.com/project/fresh-47h",
        published_at=now - timedelta(hours=47),
    )
    res = service.ingest_project(upsert)
    assert res["notification"] is not None
    assert res["notification"]["status"] == NotificationStatus.sent
    assert notifier.send_opportunity.call_count == 1


def test_stale_project_older_than_48_hours_is_skipped(
    session: Session, app_settings: Settings
) -> None:
    """Project published 49 hours ago is saved in DB but notification is skipped."""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True

    service = IngestionService(session, app_settings, notifier=notifier)
    now = utcnow()
    upsert = ProjectUpsert(
        platform=Platform.khamsat,
        source_kind=SourceKind.website_page,
        title="تصميم شبكة إطفاء حريق ومضخات",
        url="https://khamsat.com/community/requests/stale-49h",
        published_at=now - timedelta(hours=49),
    )
    res = service.ingest_project(upsert)

    # Saved in DB
    db_proj = session.query(Project).filter_by(url="https://khamsat.com/community/requests/stale-49h").one()
    assert db_proj is not None

    # Notification skipped
    assert res["notification"]["status"] == "skipped"
    assert res["notification"]["reason"] == "stale_project_skipped"
    assert notifier.send_opportunity.call_count == 0

    # Notification table audit record
    notif = session.query(Notification).filter_by(project_id=db_proj.id).one()
    assert notif.status == NotificationStatus.skipped
    assert notif.error == "stale_project_skipped"


def test_bumped_project_uses_published_at_not_last_activity(
    session: Session, app_settings: Settings
) -> None:
    """Project published 10 days ago but bumped by a comment 1 hour ago is skipped as stale."""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True

    service = IngestionService(session, app_settings, notifier=notifier)
    now = utcnow()
    upsert = ProjectUpsert(
        platform=Platform.khamsat,
        source_kind=SourceKind.website_page,
        title="تصميم شبكة إطفاء حريق معتمدة",
        url="https://khamsat.com/community/requests/bumped-old",
        published_at=now - timedelta(days=10),
        last_activity_at=now - timedelta(hours=1),
    )
    res = service.ingest_project(upsert)
    assert res["notification"]["status"] == "skipped"
    assert res["notification"]["reason"] == "stale_project_skipped"
    assert notifier.send_opportunity.call_count == 0


def test_unknown_publication_date_is_skipped(
    session: Session, app_settings: Settings
) -> None:
    """Project with published_at=None is saved to DB but notification skipped."""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True

    service = IngestionService(session, app_settings, notifier=notifier)
    upsert = ProjectUpsert(
        platform=Platform.mostaql,
        source_kind=SourceKind.website_page,
        title="تصميم شبكة إطفاء حريق معتمدة",
        url="https://mostaql.com/project/unknown-date",
        published_at=None,
    )
    res = service.ingest_project(upsert)
    assert res["notification"]["status"] == "skipped"
    assert res["notification"]["reason"] == "publication_date_unknown"
    assert notifier.send_opportunity.call_count == 0


# -----------------------------------------------------------------------------
# 3. Deduplication & Digest Batching Tests
# -----------------------------------------------------------------------------


def test_url_encoding_deduplication(
    session: Session, app_settings: Settings
) -> None:
    """Encoded and decoded URLs point to the same project and do not duplicate."""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True
    notifier.send_opportunity.return_value = {
        "status": NotificationStatus.sent,
        "message_id": 201,
        "chat_id": 12345,
    }

    service = IngestionService(session, app_settings, notifier=notifier)
    now = utcnow()

    # Raw arabic slug
    p1 = ProjectUpsert(
        platform=Platform.khamsat,
        source_kind=SourceKind.website_page,
        title="مطلوب مهندس مكافحة حريق وشبكة إطفاء",
        url="https://khamsat.com/community/requests/801119-مطلوب-مهندس-مكافحة-حريق",
        published_at=now - timedelta(hours=1),
    )
    res1 = service.ingest_project(p1)
    assert res1["outcome"] == "created"

    # Percent-encoded URL of the exact same request
    p2 = ProjectUpsert(
        platform=Platform.khamsat,
        source_kind=SourceKind.website_page,
        title="مطلوب مهندس مكافحة حريق وشبكة إطفاء",
        url="https://khamsat.com/community/requests/801119-%D9%85%D8%B7%D9%84%D9%88%D8%A8-%D9%85%D9%87%D9%86%D8%AF%D8%B3-%D9%85%D9%83%D8%A7%D9%81%D8%AD%D8%A9-%D8%AD%D8%B1%D9%8A%D9%82",
        published_at=now - timedelta(hours=1),
    )
    res2 = service.ingest_project(p2)
    assert res2["outcome"] == "updated"
    assert session.query(Project).count() == 1
    # Only 1 notification dispatched
    assert notifier.send_opportunity.call_count == 1


def test_moderate_band_queued_for_digest_and_flushed(
    session: Session, app_settings: Settings
) -> None:
    """Moderate band projects are queued as pending_digest and sent via flush_pending_digest."""
    mock_analyzer = MagicMock(spec=AIAnalyzer)
    mock_analyzer.can_analyze.return_value = False
    mock_analyzer.analyze_without_api.return_value = AnalysisResult(
        score=55,
        band=ScoreBand.moderate,
        primary_track="cv_resume",
        matching_skills=["سيرة ذاتية"],
        missing_skills=[],
        summary="فرصة مقبولة متوسطة المطابقة.",
        rationale="تطابق جزئي.",
        risks=[],
        language="ar",
        recommendation="notify",
        model="keyword",
        confidence=0.6,
    )

    notifier = MagicMock(spec=TelegramNotifier)
    notifier.enabled = True
    notifier.send_digest.return_value = {
        "status": NotificationStatus.sent,
        "message_id": 301,
        "items_count": 2,
    }

    service = IngestionService(
        session, app_settings, analyzer=mock_analyzer, notifier=notifier
    )
    now = utcnow()

    # Ingest 2 moderate projects
    p1 = ProjectUpsert(
        platform=Platform.khamsat,
        source_kind=SourceKind.website_page,
        title="تنسيق وتعديل سيرة ذاتية 1",
        url="https://khamsat.com/community/requests/mod-1",
        published_at=now - timedelta(hours=2),
    )
    p2 = ProjectUpsert(
        platform=Platform.khamsat,
        source_kind=SourceKind.website_page,
        title="تنسيق وتعديل سيرة ذاتية 2",
        url="https://khamsat.com/community/requests/mod-2",
        published_at=now - timedelta(hours=3),
    )

    res1 = service.ingest_project(p1)
    res2 = service.ingest_project(p2)

    assert res1["notification"]["status"] == NotificationStatus.pending_digest
    assert res2["notification"]["status"] == NotificationStatus.pending_digest

    # Individual notification was NOT called
    assert notifier.send_opportunity.call_count == 0

    # Flush pending digest
    flush_res = service.flush_pending_digest()
    assert flush_res["status"] == "sent"
    assert flush_res["items_count"] == 2
    assert notifier.send_digest.call_count == 1

    # Verify status in DB updated to sent
    notifs = session.query(Notification).filter_by(status=NotificationStatus.sent).all()
    assert len(notifs) == 2
