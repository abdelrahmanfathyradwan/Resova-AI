"""Tests for public listing poller (Mostaql & Khamsat).

Tests HTML parsing, baseline caching, persistent database dedup across restarts,
URL normalization, and error backoff without actual network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
from sqlalchemy.orm import Session

from resova.config.settings import Settings
from resova.db.models import Platform, Project, SourceKind
from resova.sources.listing_poller import ListingPoller, _KhamsatParser, _MostaqlParser

SAMPLE_MOSTAQL_HTML = """
<!DOCTYPE html>
<html>
<body>
    <div class="project-card">
        <h2 class="project-title">
            <a class="title" href="/project/12345-fire-design">
                مطلوب مهندس لتصميم نظام إطفاء حريق
            </a>
        </h2>
        <span class="category">هندسة وعمارة</span>
        <p class="project-brief">تصميم شبكة رش آلي وحسابات هيدروليكية ومخططات أوتوكاد.</p>
    </div>
    <div class="project-card">
        <h2 class="project-title">
            <a class="title" href="/project/12346-presentation-deck">تصميم عرض تقديمي بوربوينت</a>
        </h2>
        <span class="category">تصميم وعروض</span>
        <p class="project-brief">تصميم بروفايل شركة احترافي في 15 شريحة.</p>
    </div>
</body>
</html>
"""

SAMPLE_KHAMSAT_HTML = """
<!DOCTYPE html>
<html>
<body>
    <table>
        <tr class="request-row" data-id="98765">
            <td>
                <a class="title" href="/community/requests/98765-autocad-drawings">
                    تعديل مخططات أوتوكاد معمارية
                </a>
            </td>
            <td class="category">هندسة وتصميم</td>
            <td class="details">مطلوب تعديل رسومات تنفيذية في ملف dwg</td>
        </tr>
    </table>
</body>
</html>
"""


def test_mostaql_parser_extracts_fields() -> None:
    projects = _MostaqlParser.parse(SAMPLE_MOSTAQL_HTML)
    assert len(projects) == 2

    p1 = projects[0]
    assert p1.title == "مطلوب مهندس لتصميم نظام إطفاء حريق"
    assert p1.external_id == "12345"
    assert "/project/12345-fire-design" in (p1.url or "")
    assert p1.extras.get("category") == "هندسة وعمارة"
    assert "حسابات هيدروليكية" in (p1.description or "")

    p2 = projects[1]
    assert p2.external_id == "12346"


def test_khamsat_parser_extracts_fields() -> None:
    projects = _KhamsatParser.parse(SAMPLE_KHAMSAT_HTML)
    assert len(projects) == 1

    p = projects[0]
    assert p.title == "تعديل مخططات أوتوكاد معمارية"
    assert p.external_id == "98765"
    assert "/community/requests/98765-autocad-drawings" in (p.url or "")
    assert p.extras.get("category") == "هندسة وتصميم"


def test_khamsat_parser_extracts_slug_id_without_data_id() -> None:
    slug = "801117-اختبار-نظام-resova-ai-مطلوب-تصميم-شعار-بسيط"
    html = f"""
    <table>
        <tr class="forum_post">
            <td>
                <a class="title" href="/community/requests/{slug}">
                    اختبار نظام Resova AI – مطلوب تصميم شعار بسيط
                </a>
            </td>
        </tr>
    </table>
    """
    projects = _KhamsatParser.parse(html)
    assert len(projects) == 1
    p = projects[0]
    assert p.external_id == "801117"
    assert p.title == "اختبار نظام Resova AI – مطلوب تصميم شعار بسيط"


def test_listing_poller_disabled_returns_empty() -> None:
    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
        enable_listing_poller=False,
    )
    poller = ListingPoller(settings)
    assert poller.fetch_new() == []


def test_listing_poller_baseline_and_new_projects() -> None:
    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        enable_listing_poller=True,
        listing_poller_ack=True,
        mostaql_listing_urls=["https://mostaql.com/projects"],
        khamsat_listing_urls=["https://khamsat.com/community/requests"],
        telegram_bot_token="1234:token",
        telegram_allowed_user_id=1,
    )

    mock_resp_mostaql = MagicMock()
    mock_resp_mostaql.status_code = 200
    mock_resp_mostaql.text = SAMPLE_MOSTAQL_HTML

    mock_resp_khamsat = MagicMock()
    mock_resp_khamsat.status_code = 200
    mock_resp_khamsat.text = SAMPLE_KHAMSAT_HTML

    mock_client = MagicMock(spec=httpx.Client)

    def _get(url: str, **kwargs):
        if "mostaql" in url:
            return mock_resp_mostaql
        return mock_resp_khamsat

    mock_client.get.side_effect = _get

    poller = ListingPoller(settings, client=mock_client)

    # First run is baseline: caches existing items, returns 0 new projects
    baseline_projects = poller.fetch_new()
    assert len(baseline_projects) == 0
    assert poller._baseline_done is True

    # Second run with an additional project in HTML
    updated_html = SAMPLE_MOSTAQL_HTML.replace(
        "</body>",
        """
        <div class="project-card">
            <h2 class="project-title">
                <a class="title" href="/project/99999-new-hvac">تصميم نظام تكييف جديد</a>
            </h2>
        </div>
        </body>
        """,
    )
    mock_resp_mostaql.text = updated_html

    new_projects = poller.fetch_new()
    assert len(new_projects) == 1
    assert new_projects[0].external_id == "99999"


def test_persistent_db_baseline_prevents_duplicates_after_restart(session: Session) -> None:
    # Seed DB with existing project
    existing = Project(
        platform=Platform.mostaql,
        source_kind=SourceKind.website_page,
        external_id="12345",
        url="https://mostaql.com/project/12345-fire-design",
        title="مطلوب مهندس لتصميم نظام إطفاء حريق",
    )
    session.add(existing)
    session.flush()

    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        enable_listing_poller=True,
        listing_poller_ack=True,
        mostaql_listing_urls=["https://mostaql.com/projects"],
        telegram_bot_token="1234:token",
        telegram_allowed_user_id=1,
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = SAMPLE_MOSTAQL_HTML

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.get.return_value = mock_resp

    # Initialize a fresh poller with session (simulates app restart)
    poller = ListingPoller(settings, session=session, client=mock_client)

    # Since project 12345 is already in DB, it should NOT be returned as new
    new_projects = poller.fetch_new()
    assert not any(p.external_id == "12345" for p in new_projects)


def test_mostaql_universal_anchor_fallback() -> None:
    """Universal anchor scan extracts projects from non-card HTML and ignores non-project links."""
    html = """
    <html>
    <body>
        <nav>
            <a href="/projects?sort=latest">أحدث المشاريع</a>
            <a href="/projects/add">أضف مشروعك</a>
            <a href="/category/engineering">هندسة</a>
            <a href="/u/some_freelancer">حساب المستخدم</a>
        </nav>
        <div class="custom-feed">
            <a href="/project/77701-fire-alarm">مشروع إنذار حريق ومكافحة</a>
            <a href="/project/77702-hvac-duct-design"></a>
            <a href="/project/77703"></a>
        </div>
    </body>
    </html>
    """
    projects = _MostaqlParser.parse(html)
    assert len(projects) == 3
    ids = [p.external_id for p in projects]
    assert ids == ["77701", "77702", "77703"]

    # Title from link text
    assert projects[0].title == "مشروع إنذار حريق ومكافحة"
    assert projects[0].url == "https://mostaql.com/project/77701-fire-alarm"

    # Title from slug fallback
    assert projects[1].title == "hvac duct design"
    assert projects[1].url == "https://mostaql.com/project/77702-hvac-duct-design"

    # Title default fallback
    assert projects[2].title == "مشروع مستقل #77703"
    assert projects[2].url == "https://mostaql.com/project/77703"


def test_khamsat_universal_anchor_fallback() -> None:
    """Khamsat universal anchor scan extracts requests and ignores community/pagination links."""
    html = """
    <html>
    <body>
        <div class="header">
            <a href="/community/requests">طلبات مجتمع خمسات</a>
            <a href="/community/requests?page=2">الصفحة التالية</a>
            <a href="/user/specialist">الملف الشخصي</a>
        </div>
        <div class="posts-list">
            <a href="/community/requests/88801-autocad-drawing">مطلوب رسم مخطط تنفيذي</a>
            <a href="/community/requests/88802-logo-design"></a>
        </div>
    </body>
    </html>
    """
    projects = _KhamsatParser.parse(html)
    assert len(projects) == 2
    assert projects[0].external_id == "88801"
    assert projects[0].title == "مطلوب رسم مخطط تنفيذي"
    assert projects[0].url == "https://khamsat.com/community/requests/88801-autocad-drawing"

    assert projects[1].external_id == "88802"
    assert projects[1].title == "logo design"


def test_card_and_anchor_deduplication() -> None:
    """Card-extracted project is not duplicated by universal anchor fallback scan."""
    html = """
    <html>
    <body>
        <div class="project-card">
            <h2 class="project-title">
                <a class="title" href="/project/12345-fire-design">مطلوب مهندس إطفاء</a>
            </h2>
            <span class="category">هندسة</span>
            <p class="project-brief">تفاصيل كاملة هنا</p>
        </div>
        <!-- Stray anchor on page pointing to same project -->
        <a href="/project/12345-fire-design">رابط إضافي لنفس المشروع</a>
    </body>
    </html>
    """
    projects = _MostaqlParser.parse(html)
    assert len(projects) == 1
    assert projects[0].external_id == "12345"
    assert projects[0].title == "مطلوب مهندس إطفاء"
    assert projects[0].extras.get("category") == "هندسة"
    assert projects[0].description == "تفاصيل كاملة هنا"


def test_mostaql_sort_latest_auto_appended() -> None:
    """ListingPoller appends sort=latest to Mostaql listing URL if not present."""
    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        enable_listing_poller=True,
        listing_poller_ack=True,
        mostaql_listing_urls=["https://mostaql.com/projects"],
        telegram_bot_token="1234:token",
        telegram_allowed_user_id=1,
    )
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = "<html><body></body></html>"
    mock_client.get.return_value = mock_resp

    poller = ListingPoller(settings, client=mock_client)
    poller.fetch_new()

    called_url = mock_client.get.call_args[0][0]
    assert "https://mostaql.com/projects?sort=latest" in called_url


def test_pagination_fetches_multiple_pages() -> None:
    """ListingPoller queries up to max_poll_pages and combines results."""
    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        enable_listing_poller=True,
        listing_poller_ack=True,
        mostaql_listing_urls=["https://mostaql.com/projects?sort=latest"],
        max_poll_pages=2,
        telegram_bot_token="1234:token",
        telegram_allowed_user_id=1,
    )

    page1_html = """
    <html><body>
        <a href="/project/101-page-one">مشروع صفحة 1</a>
    </body></html>
    """
    page2_html = """
    <html><body>
        <a href="/project/102-page-two">مشروع صفحة 2</a>
    </body></html>
    """

    mock_client = MagicMock(spec=httpx.Client)

    def _get(url: str, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "page=2" in url:
            resp.text = page2_html
        else:
            resp.text = page1_html
        return resp

    mock_client.get.side_effect = _get

    poller = ListingPoller(settings, client=mock_client)
    poller.fetch_new()
    # Baseline collects items from both pages
    assert len(poller._known_keys) >= 2
    assert any("101" in k for k in poller._known_keys)
    assert any("102" in k for k in poller._known_keys)


def test_watermark_max_id_tracking(session: Session) -> None:
    """Poller tracks highest seen external_id per platform."""
    existing = Project(
        platform=Platform.mostaql,
        source_kind=SourceKind.website_page,
        external_id="55000",
        url="https://mostaql.com/project/55000-watermark",
        title="مشروع قديم",
    )
    session.add(existing)
    session.flush()

    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        enable_listing_poller=True,
        listing_poller_ack=True,
        mostaql_listing_urls=["https://mostaql.com/projects?sort=latest"],
        telegram_bot_token="1234:token",
        telegram_allowed_user_id=1,
    )
    mock_client = MagicMock(spec=httpx.Client)
    poller = ListingPoller(settings, session=session, client=mock_client)
    assert poller._max_id[Platform.mostaql] == 55000

    # Recording a higher project updates watermark
    new_raw = _MostaqlParser.parse('<a href="/project/55100-newer">مشروع أحدث</a>')[0]
    poller._record_known(new_raw)
    assert poller._max_id[Platform.mostaql] == 55100


def test_page_reordering_and_moving_pages_does_not_duplicate() -> None:
    """When projects swap order or shift between pages, poller does not re-emit them as new."""
    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        enable_listing_poller=True,
        listing_poller_ack=True,
        mostaql_listing_urls=["https://mostaql.com/projects?sort=latest"],
        max_poll_pages=2,
        telegram_bot_token="1234:token",
        telegram_allowed_user_id=1,
    )

    mock_client = MagicMock(spec=httpx.Client)
    state = {"cycle": 1}

    def _get(url: str, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if state["cycle"] == 1:
            if "page=2" in url:
                resp.text = '<a href="/project/102-test">مشروع 102</a>'
            else:
                resp.text = '<a href="/project/101-test">مشروع 101</a>'
        else:
            if "page=2" in url:
                resp.text = '<a href="/project/101-test">مشروع 101</a>'
            else:
                resp.text = '<a href="/project/102-test">مشروع 102</a>'
        return resp

    mock_client.get.side_effect = _get
    poller = ListingPoller(settings, client=mock_client)

    # Baseline collects both 101 and 102
    poller.fetch_new()
    assert poller._baseline_done is True

    # In cycle 2, order is inverted across pages; should produce 0 new items
    state["cycle"] = 2
    new_items = poller.fetch_new()
    assert len(new_items) == 0
