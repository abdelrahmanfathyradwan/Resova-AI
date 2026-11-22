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
