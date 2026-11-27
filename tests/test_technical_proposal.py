"""Comprehensive tests for the AI Technical Proposal Generator feature.

Covers:
1. Structured 9-section draft creation from existing projects (fallback & AI).
2. Different proposals tailored to different project domains.
3. Persistence, retrieval, and editor status progression (draft -> edited -> ready).
4. Section-level and full regeneration.
5. Duplicating proposals and exporting Arabic RTL PDFs.
6. Ownership-based access control (preventing cross-user access).
7. Graceful handling of AI provider failures.
8. Handling projects with missing description, analysis, or user profile.
9. Anti-fabrication rules (no invented prices, experience, or portfolio items).
10. Dashboard HTML & REST API integration endpoints.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from resova.api.app import create_app
from resova.api.dashboard import compute_access_code
from resova.config.settings import Settings
from resova.db.base import Base
from resova.db.models import (
    Platform,
    Project,
    ProjectAnalysis,
    ProposalDraft,
    ProposalEditorStatus,
    ScoreBand,
    SourceKind,
    TelegramUser,
    TelegramUserRole,
    TelegramUserStatus,
)
from resova.db.session import create_db_engine, create_session_factory
from resova.services.pdf_exporter import export_proposal_pdf
from resova.services.technical_proposal import (
    SECTION_KEYS,
    ProposalSections,
    TechnicalProposalService,
    UserProfessionalProfileData,
)
from resova.utils.errors import AIError


@pytest.fixture()
def tech_settings() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
    )


@pytest.fixture()
def user_a(session: Session) -> TelegramUser:
    user = TelegramUser(
        telegram_chat_id=100001,
        username="eng_ahmed",
        first_name="أحمد",
        role=TelegramUserRole.owner,
        status=TelegramUserStatus.active,
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture()
def user_b(session: Session) -> TelegramUser:
    user = TelegramUser(
        telegram_chat_id=100002,
        username="designer_sara",
        first_name="سارة",
        role=TelegramUserRole.user,
        status=TelegramUserStatus.active,
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture()
def hvac_project(session: Session) -> Project:
    project = Project(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="تصميم مخططات التكييف HVAC وحساب الأحمال الحرارية لفيلا سكنية",
        description=(
            "مطلوب مهندس ميكانيكا لتصميم مخططات التكييف المركزي وحساب الأحمال "
            "الحرارية عبر برنامج HAP ورسم مسارات الدكت على AutoCAD."
        ),
        category="هندسة وعمارة",
        duration="14 يوم",
    )
    session.add(project)
    session.flush()
    analysis = ProjectAnalysis(
        project_id=project.id,
        score=88,
        band=ScoreBand.strong,
        track="engineering_mep",
        summary="مشروع تصميم تكييف وحساب أحمال حرارية مناسب جدًا للتخصص.",
        rationale="يتطابق مع مهارات MEP والتكييف والـ AutoCAD.",
        model="gpt-4o-mini",
    )
    session.add(analysis)
    session.flush()
    return project


@pytest.fixture()
def deck_project(session: Session) -> Project:
    project = Project(
        platform=Platform.khamsat,
        source_kind=SourceKind.manual_paste,
        title="تصميم عرض تقديمي احترافي Pitch Deck لشركة تقنية ناشئة",
        description="نحتاج إعداد عرض تقديمي من 15 شريحة للمستثمرين باللغة العربية.",
        category="تصميم",
    )
    session.add(project)
    session.flush()
    return project


def test_create_draft_from_existing_project(
    session: Session,
    tech_settings: Settings,
    user_a: TelegramUser,
    hvac_project: Project,
) -> None:
    svc = TechnicalProposalService(session, tech_settings)
    draft = svc.generate_technical_proposal(hvac_project.id, user_a.id)

    assert isinstance(draft, ProposalDraft)
    assert draft.project_id == hvac_project.id
    assert draft.owner_user_id == user_a.id
    assert draft.editor_status == ProposalEditorStatus.draft
    assert draft.is_ai_generated is False
    assert draft.cost_text == ""
    assert "تقديرية" in (draft.timeline_text or "")

    sections = svc.parse_sections(draft)
    for key in SECTION_KEYS:
        val = getattr(sections, key)
        if key == "cost":
            assert val == ""
        else:
            assert val.strip() != ""
    assert "المقدمة" in draft.content


def test_different_proposals_for_different_projects(
    session: Session,
    tech_settings: Settings,
    user_a: TelegramUser,
    hvac_project: Project,
    deck_project: Project,
) -> None:
    svc = TechnicalProposalService(session, tech_settings)
    d1 = svc.generate_technical_proposal(hvac_project.id, user_a.id)
    d2 = svc.generate_technical_proposal(deck_project.id, user_a.id)

    s1 = svc.parse_sections(d1)
    s2 = svc.parse_sections(d2)

    assert s1.proposed_solution != s2.proposed_solution
    assert s1.deliverables != s2.deliverables
    assert "هندسية" in s1.proposed_solution
    assert "شرائح" in s2.proposed_solution or "عرض تقديمي" in s2.proposed_solution


def test_save_and_retrieve_draft(
    session: Session,
    tech_settings: Settings,
    user_a: TelegramUser,
    hvac_project: Project,
) -> None:
    svc = TechnicalProposalService(session, tech_settings)
    draft = svc.generate_technical_proposal(hvac_project.id, user_a.id)
    sections = svc.parse_sections(draft)
    sections.cost = "1500 ريال سعودي (شامل المراجعات)"
    sections.intro = "مقدمة مخصصة بعد المراجعة."

    updated = svc.update_proposal(
        draft.id,
        user_a.id,
        title="عرض فني نهائي - فيلا",
        sections=sections,
        editor_status=ProposalEditorStatus.ready,
    )

    fetched = svc.get_proposal(updated.id, user_a.id)
    assert fetched.title == "عرض فني نهائي - فيلا"
    assert fetched.editor_status == ProposalEditorStatus.ready
    assert fetched.cost_text == "1500 ريال سعودي (شامل المراجعات)"
    parsed = svc.parse_sections(fetched)
    assert parsed.intro == "مقدمة مخصصة بعد المراجعة."


def test_edit_and_regenerate_section_and_full(
    session: Session,
    tech_settings: Settings,
    user_a: TelegramUser,
    hvac_project: Project,
) -> None:
    svc = TechnicalProposalService(session, tech_settings)
    draft = svc.generate_technical_proposal(hvac_project.id, user_a.id)

    sections = svc.parse_sections(draft)
    sections.intro = "مقدمة معدلة يدويًا."
    sections.methodology = "منهجية قديمة"
    svc.update_proposal(draft.id, user_a.id, sections=sections)

    regen_sec = svc.regenerate_section(draft.id, user_a.id, "methodology")
    after_sec = svc.parse_sections(regen_sec)
    assert after_sec.intro == "مقدمة معدلة يدويًا."
    assert after_sec.methodology != "منهجية قديمة"
    assert regen_sec.editor_status == ProposalEditorStatus.edited

    regen_all = svc.regenerate_full(draft.id, user_a.id)
    after_all = svc.parse_sections(regen_all)
    assert after_all.intro != "مقدمة معدلة يدويًا."


def test_duplicate_proposal_and_export_pdf(
    session: Session,
    tech_settings: Settings,
    user_a: TelegramUser,
    hvac_project: Project,
) -> None:
    svc = TechnicalProposalService(session, tech_settings)
    svc.upsert_professional_profile(
        user_a.id,
        UserProfessionalProfileData(
            professional_name="م. أحمد علي",
            specialization="مهندس ميكانيكا HVAC",
            tools_software="AutoCAD, Revit, HAP",
        ),
    )
    draft = svc.generate_technical_proposal(hvac_project.id, user_a.id)
    copy_draft = svc.duplicate_proposal(draft.id, user_a.id)

    assert copy_draft.id != draft.id
    assert copy_draft.version == draft.version + 1
    assert "نسخة" in (copy_draft.title or "")

    sections = svc.parse_sections(copy_draft)
    prof = svc.get_professional_profile(user_a.id)
    pdf_bytes = export_proposal_pdf(copy_draft, hvac_project, sections, prof)

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 2000


def test_prevent_access_to_another_users_drafts(
    session: Session,
    tech_settings: Settings,
    user_a: TelegramUser,
    user_b: TelegramUser,
    hvac_project: Project,
) -> None:
    svc = TechnicalProposalService(session, tech_settings)
    draft_a = svc.generate_technical_proposal(hvac_project.id, user_a.id)

    assert len(svc.list_project_proposals(hvac_project.id, user_a.id)) == 1
    assert len(svc.list_project_proposals(hvac_project.id, user_b.id)) == 0

    with pytest.raises(PermissionError):
        svc.get_proposal(draft_a.id, user_b.id)

    with pytest.raises(PermissionError):
        svc.update_proposal(
            draft_a.id,
            user_b.id,
            sections=svc.parse_sections(draft_a),
            title="محاولة تعديل",
        )

    with pytest.raises(PermissionError):
        svc.regenerate_section(draft_a.id, user_b.id, "intro")

    with pytest.raises(PermissionError):
        svc.duplicate_proposal(draft_a.id, user_b.id)

    with pytest.raises(PermissionError):
        svc.delete_proposal(draft_a.id, user_b.id)


def test_handle_ai_provider_failure_gracefully(
    session: Session,
    user_a: TelegramUser,
    hvac_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_no_key = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        openai_api_key=None,
        enable_telegram=False,
    )
    svc = TechnicalProposalService(session, settings_no_key)
    with pytest.raises(AIError):
        svc.generate_technical_proposal(hvac_project.id, user_a.id, require_ai=True)

    # With require_ai=False, failure falls back cleanly to structured template
    settings_bad_ai = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=True,
        openai_api_key="sk-test-dummy-key",
        enable_telegram=False,
    )
    svc2 = TechnicalProposalService(session, settings_bad_ai)
    monkeypatch.setattr(
        svc2,
        "_call_openai_full",
        MagicMock(side_effect=AIError("OpenAI timeout")),
    )
    draft = svc2.generate_technical_proposal(hvac_project.id, user_a.id, require_ai=False)
    assert draft.is_ai_generated is False
    assert draft.cost_text == ""


def test_handle_missing_project_data(
    session: Session,
    tech_settings: Settings,
    user_a: TelegramUser,
) -> None:
    minimal_project = Project(
        platform=Platform.mostaql,
        source_kind=SourceKind.manual_paste,
        title="مراجعة مخططات معمارية",
        description=None,
    )
    session.add(minimal_project)
    session.flush()

    svc = TechnicalProposalService(session, tech_settings)
    draft = svc.generate_technical_proposal(minimal_project.id, user_a.id)
    sections = svc.parse_sections(draft)

    assert "مراجعة مخططات معمارية" in sections.understanding
    assert sections.scope_of_work.strip() != ""
    assert sections.cost == ""


def test_no_fabricated_professional_info_or_prices(
    session: Session,
    user_a: TelegramUser,
    hvac_project: Project,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=True,
        openai_api_key="sk-test-dummy-key",
        enable_telegram=False,
    )
    svc = TechnicalProposalService(session, settings)

    # Even if mocked AI tries to return a price, service must strip cost to blank
    fake_ai_sections = ProposalSections(
        intro="مرحبًا بكم",
        understanding="فهم المشروع",
        proposed_solution="الحل المقترح",
        scope_of_work="نطاق العمل",
        methodology="المنهجية",
        deliverables="التسليمات",
        timeline="",
        cost="5000 دولار",
        closing="شكرًا لكم",
    )
    monkeypatch.setattr(svc, "_call_openai_full", lambda *_args, **_kw: fake_ai_sections)

    draft = svc.generate_technical_proposal(hvac_project.id, user_a.id, require_ai=True)
    assert draft.is_ai_generated is True
    assert draft.cost_text == ""
    assert svc.parse_sections(draft).cost == ""
    assert "تقديرية" in (draft.timeline_text or "")

    # Without user profile, fallback should not invent fake experience/names
    svc_no_ai = TechnicalProposalService(
        session,
        Settings(
            _env_file=None,
            enable_email_ingestion=False,
            enable_ai_analysis=False,
            enable_telegram=False,
        ),
    )
    fallback_draft = svc_no_ai.generate_technical_proposal(hvac_project.id, user_a.id)
    text = fallback_draft.content
    assert "10 سنوات" not in text
    assert "شركة أرامكو" not in text


@pytest.fixture()
def dashboard_app_env(tmp_path: Path) -> Iterator[tuple[TestClient, Settings, int, int, int]]:
    db_url = f"sqlite:///{(tmp_path / 'dash-test.db').as_posix()}"
    settings = Settings(
        _env_file=None,
        database_url=db_url,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
        telegram_bot_token="123456:TEST-BOT-TOKEN",
    )
    eng = create_db_engine(db_url)
    Base.metadata.create_all(eng)
    factory = create_session_factory(eng)
    with factory() as sess:
        ua = TelegramUser(
            telegram_chat_id=555001,
            username="owner_u",
            first_name="مالك",
            role=TelegramUserRole.owner,
            status=TelegramUserStatus.active,
        )
        ub = TelegramUser(
            telegram_chat_id=555002,
            username="other_u",
            first_name="آخر",
            role=TelegramUserRole.user,
            status=TelegramUserStatus.active,
        )
        proj = Project(
            platform=Platform.mostaql,
            source_kind=SourceKind.manual_paste,
            title="تصميم إنذار حريق",
            description="مطلوب مخططات إنذار حريق وفق الكود السعودي.",
        )
        sess.add_all([ua, ub, proj])
        sess.commit()
        ua_chat, ub_chat, proj_id = ua.telegram_chat_id, ub.telegram_chat_id, proj.id
    eng.dispose()

    app = create_app(settings)
    with TestClient(app) as client:
        yield client, settings, ua_chat, ub_chat, proj_id


def test_dashboard_and_rest_api_flow_and_isolation(
    dashboard_app_env: tuple[TestClient, Settings, int, int, int],
) -> None:
    client, settings, ua_chat, ub_chat, proj_id = dashboard_app_env

    # Unauthenticated API call -> 401
    assert client.post(f"/api/projects/{proj_id}/technical-proposals").status_code == 401

    # Login as user A
    code_a = compute_access_code(ua_chat, settings)
    resp_login = client.post(
        "/dashboard/login",
        data={"chat_id": ua_chat, "access_code": code_a},
        follow_redirects=False,
    )
    assert resp_login.status_code == 303

    # Project detail page shows the required button
    detail_html = client.get(f"/dashboard/projects/{proj_id}").text
    assert "إنشاء عرض فني بالذكاء الاصطناعي" in detail_html

    # Generate proposal via REST API
    gen_resp = client.post(f"/api/projects/{proj_id}/technical-proposals", json={})
    assert gen_resp.status_code == 201
    draft_id = gen_resp.json()["id"]

    # Export PDF via REST API
    pdf_resp = client.get(f"/api/proposals/{draft_id}/pdf")
    assert pdf_resp.status_code == 200
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert pdf_resp.content.startswith(b"%PDF-")

    # Login as user B and verify 403 isolation on user A's draft
    code_b = compute_access_code(ub_chat, settings)
    client.post("/dashboard/login", data={"chat_id": ub_chat, "access_code": code_b})
    assert client.get(f"/api/proposals/{draft_id}").status_code == 403
    assert client.get(f"/api/proposals/{draft_id}/pdf").status_code == 403
    assert client.delete(f"/api/proposals/{draft_id}").status_code == 403
    assert client.get(f"/dashboard/proposals/{draft_id}").status_code == 403


def test_telegram_notification_sent_with_open_button_and_owner_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from resova.telegram.bot import TelegramNotifier

    db_url = f"sqlite:///{(tmp_path / 'tg-notif.db').as_posix()}"
    settings = Settings(
        _env_file=None,
        database_url=db_url,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token="987654:SECRET-BOT-TOKEN",
        telegram_allowed_user_id=777001,
        telegram_owner_id=777001,
        public_base_url="https://resova-production.up.railway.app",
    )
    eng = create_db_engine(db_url)
    Base.metadata.create_all(eng)
    factory = create_session_factory(eng)
    with factory() as sess:
        ua = TelegramUser(
            telegram_chat_id=777001,
            username="tg_owner",
            first_name="مهندس",
            role=TelegramUserRole.owner,
            status=TelegramUserStatus.active,
        )
        proj = Project(
            platform=Platform.mostaql,
            source_kind=SourceKind.manual_paste,
            title="تصميم مخططات تكييف",
            description="مشروع تكييف مركزي",
        )
        sess.add_all([ua, proj])
        sess.commit()
        ua_chat, proj_id = ua.telegram_chat_id, proj.id
    eng.dispose()

    sent_calls: list[dict[str, object]] = []

    def fake_send_message(
        self: TelegramNotifier,
        text: str,
        chat_id: int | None = None,
        reply_markup: dict[str, object] | None = None,
        parse_mode: str | None = "Markdown",
    ) -> dict[str, object]:
        sent_calls.append(
            {
                "text": text,
                "chat_id": chat_id,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
            }
        )
        return {"message_id": 4242, "ok": True}

    monkeypatch.setattr(TelegramNotifier, "_send_message", fake_send_message)

    app = create_app(settings)
    with TestClient(app) as client:
        code_a = compute_access_code(ua_chat, settings)
        client.post("/dashboard/login", data={"chat_id": ua_chat, "access_code": code_a})
        res = client.post(f"/api/projects/{proj_id}/technical-proposals", json={})
        assert res.status_code == 201
        body = res.json()
        draft_id = body["id"]
        assert body["telegram_notified"] is True
        assert body["telegram_warning"] is None

        assert len(sent_calls) == 1
        call = sent_calls[0]
        assert call["chat_id"] == ua_chat
        assert "تم إنشاء مسودتك الفنية بنجاح" in str(call["text"])
        assert "تصميم مخططات تكييف" in str(call["text"])
        kb = call["reply_markup"]["inline_keyboard"]  # type: ignore[index]
        assert kb[0][0]["text"] == "📄 فتح العرض الفني"
        expected_url = f"https://resova-production.up.railway.app/dashboard/proposals/{draft_id}"
        assert kb[0][0]["url"] == expected_url
        assert "SECRET-BOT-TOKEN" not in expected_url

        # Owner can open the dashboard proposal page
        page = client.get(f"/dashboard/proposals/{draft_id}")
        assert page.status_code == 200
        assert "محرر العرض الفني" in page.text


def test_telegram_notification_failure_and_unlinked_user_keep_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from resova.telegram.bot import TelegramNotifier
    from resova.utils.errors import TelegramError

    db_url = f"sqlite:///{(tmp_path / 'tg-fail.db').as_posix()}"
    settings = Settings(
        _env_file=None,
        database_url=db_url,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token="987654:SECRET-BOT-TOKEN",
        telegram_allowed_user_id=777002,
        telegram_owner_id=777002,
    )
    eng = create_db_engine(db_url)
    Base.metadata.create_all(eng)
    factory = create_session_factory(eng)
    with factory() as sess:
        ua = TelegramUser(
            telegram_chat_id=777002,
            username="tg_fail_u",
            first_name="مستخدم",
            role=TelegramUserRole.owner,
            status=TelegramUserStatus.active,
        )
        proj = Project(
            platform=Platform.mostaql,
            source_kind=SourceKind.manual_paste,
            title="مشروع اختبار فشل تيليجرام",
            description="وصف المشروع",
        )
        sess.add_all([ua, proj])
        sess.commit()
        ua_chat, proj_id = ua.telegram_chat_id, proj.id
    eng.dispose()

    def failing_send(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise TelegramError("Simulated network failure")

    monkeypatch.setattr(TelegramNotifier, "_send_message", failing_send)

    app = create_app(settings)
    with TestClient(app) as client:
        code_a = compute_access_code(ua_chat, settings)
        client.post("/dashboard/login", data={"chat_id": ua_chat, "access_code": code_a})
        res = client.post(f"/api/projects/{proj_id}/technical-proposals", json={})
        assert res.status_code == 201
        body = res.json()
        assert body["telegram_notified"] is False
        assert body["telegram_warning"]
        # Draft is persisted despite Telegram failure
        assert client.get(f"/api/proposals/{body['id']}").status_code == 200


def test_telegram_proposal_notification_retries_and_no_duplicates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from resova.telegram.bot import TelegramNotifier
    from resova.utils.errors import TelegramError

    settings = Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token="987654:SECRET-BOT-TOKEN",
        telegram_allowed_user_id=777003,
        telegram_owner_id=777003,
    )
    notifier = TelegramNotifier(settings)
    attempts = 0

    def flaky_send(
        self: TelegramNotifier,
        text: str,
        chat_id: int | None = None,
        reply_markup: dict[str, object] | None = None,
        parse_mode: str | None = "Markdown",
    ) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TelegramError("Transient 502")
        return {"message_id": 999, "ok": True}

    monkeypatch.setattr(TelegramNotifier, "_send_message", flaky_send)

    first = notifier.send_proposal_created_notification(
        chat_id=777003,
        draft_id=123,
        project_title="مشروع",
        proposal_title="عرض",
        proposal_url="https://resova-production.up.railway.app/dashboard/proposals/123",
    )
    assert first["sent"] is True
    assert first["duplicate"] is False
    assert attempts == 2

    # Second call for the same draft_id must not send duplicate message
    second = notifier.send_proposal_created_notification(
        chat_id=777003,
        draft_id=123,
        project_title="مشروع",
        proposal_title="عرض",
        proposal_url="https://resova-production.up.railway.app/dashboard/proposals/123",
    )
    assert second["duplicate"] is True
    assert attempts == 2
