"""Tests for automatic technical proposal generation on matching Mostaql projects."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session, sessionmaker

from resova.config.settings import Settings
from resova.db.base import Base
from resova.db.models import (
    AutoProposalJob,
    AutoProposalJobStatus,
    Platform,
    Project,
    ProposalDraft,
    ProposalEditorStatus,
    SourceKind,
    TelegramUser,
    TelegramUserRole,
    TelegramUserStatus,
)
from resova.db.session import create_db_engine, create_session_factory
from resova.services.auto_proposal import (
    enqueue_if_eligible,
    process_next_pending_job,
    process_pending_jobs,
)
from resova.services.technical_proposal import ProposalSections, TechnicalProposalService
from resova.telegram.bot import TelegramNotifier
from resova.utils.errors import AIError, TelegramError


@pytest.fixture()
def auto_env(tmp_path: Path) -> tuple[sessionmaker[Session], Settings, str]:
    db_url = f"sqlite:///{(tmp_path / 'auto-prop.db').as_posix()}"
    settings = Settings(
        _env_file=None,
        database_url=db_url,
        enable_email_ingestion=False,
        enable_ai_analysis=True,
        openai_api_key="sk-test-dummy-key",
        enable_telegram=True,
        telegram_bot_token="123456:TEST-BOT-TOKEN",
        telegram_allowed_user_id=888001,
        telegram_owner_id=888001,
    )
    eng = create_db_engine(db_url)
    Base.metadata.create_all(eng)
    factory = create_session_factory(eng)
    with factory() as sess:
        owner = TelegramUser(
            telegram_chat_id=888001,
            username="auto_owner",
            first_name="مهندس",
            role=TelegramUserRole.owner,
            status=TelegramUserStatus.active,
        )
        other = TelegramUser(
            telegram_chat_id=888002,
            username="other_user",
            first_name="آخر",
            role=TelegramUserRole.user,
            status=TelegramUserStatus.active,
        )
        sess.add_all([owner, other])
        sess.commit()
    eng.dispose()
    return factory, settings, db_url


def _add_project(
    sess: Session,
    *,
    platform: Platform = Platform.mostaql,
    title: str = "تصميم مخططات تكييف HVAC",
) -> Project:
    proj = Project(
        platform=platform,
        source_kind=SourceKind.website_page,
        title=title,
        description="مطلوب تصميم مخططات التكييف وحساب الأحمال الحرارية.",
    )
    sess.add(proj)
    sess.commit()
    return proj


def _fake_sections(*_args: object, **_kwargs: object) -> ProposalSections:
    return ProposalSections(
        intro="مقدمة احترافية",
        understanding="فهم واضح لمتطلبات التكييف",
        proposed_solution="تصميم مخططات HVAC وفق المعايير",
        scope_of_work="حساب الأحمال ورسم الدكت",
        methodology="مراجعة -> تصميم -> تسليم",
        deliverables="ملفات AutoCAD وPDF",
        timeline="مدة تقديرية مبدئية 7 أيام",
        cost="",
        closing="يسعدني تواصلكم",
    )


def test_auto_proposal_threshold_80_65_and_below_65(
    auto_env: tuple[sessionmaker[Session], Settings, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, settings, _ = auto_env
    monkeypatch.setattr(TechnicalProposalService, "_call_openai_full", _fake_sections)
    sent_msgs: list[int] = []
    monkeypatch.setattr(
        TelegramNotifier,
        "_send_message",
        lambda self, text, chat_id=None, reply_markup=None, parse_mode=None: (
            sent_msgs.append(chat_id or 0) or {"message_id": 10, "ok": True}
        ),
    )

    with factory() as sess:
        p80 = _add_project(sess, title="مشروع 80%")
        p65 = _add_project(sess, title="مشروع 65%")
        p64 = _add_project(sess, title="مشروع 64%")
        p_kh = _add_project(sess, platform=Platform.khamsat, title="مشروع خمسات")

        assert enqueue_if_eligible(sess, settings, p80.id, 80) is not None
        assert enqueue_if_eligible(sess, settings, p65.id, 65) is not None
        assert enqueue_if_eligible(sess, settings, p64.id, 64) is None
        assert enqueue_if_eligible(sess, settings, p_kh.id, 90) is None
        sess.commit()

    results = process_pending_jobs(factory, settings)
    assert len(results) == 2
    assert all(r["status"] == "generated" for r in results)
    assert sent_msgs == [888001, 888001]

    with factory() as sess:
        drafts = sess.query(ProposalDraft).all()
        assert len(drafts) == 2
        assert all(d.is_ai_generated is True for d in drafts)
        assert all(d.editor_status == ProposalEditorStatus.draft for d in drafts)


def test_idempotency_across_rediscovery_and_restart(
    auto_env: tuple[sessionmaker[Session], Settings, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, settings, db_url = auto_env
    monkeypatch.setattr(TechnicalProposalService, "_call_openai_full", _fake_sections)
    monkeypatch.setattr(
        TelegramNotifier,
        "_send_message",
        lambda *a, **kw: {"message_id": 11, "ok": True},
    )

    with factory() as sess:
        proj = _add_project(sess)
        j1 = enqueue_if_eligible(sess, settings, proj.id, 85)
        j2 = enqueue_if_eligible(sess, settings, proj.id, 85)
        sess.commit()
        assert j1 is not None and j2 is not None and j1.id == j2.id

    process_pending_jobs(factory, settings)

    # Simulate app restart with a brand new engine/session_factory
    new_eng = create_db_engine(db_url)
    new_factory = create_session_factory(new_eng)
    try:
        with new_factory() as sess:
            j3 = enqueue_if_eligible(sess, settings, proj.id, 85)
            sess.commit()
            assert j3 is not None
            assert j3.status == AutoProposalJobStatus.generated
        res = process_pending_jobs(new_factory, settings)
        assert res == []
        with new_factory() as sess:
            assert sess.query(ProposalDraft).count() == 1
    finally:
        new_eng.dispose()


def test_parallel_workers_do_not_duplicate_drafts(
    auto_env: tuple[sessionmaker[Session], Settings, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, settings, _ = auto_env
    monkeypatch.setattr(TechnicalProposalService, "_call_openai_full", _fake_sections)
    monkeypatch.setattr(
        TelegramNotifier,
        "_send_message",
        lambda *a, **kw: {"message_id": 12, "ok": True},
    )

    with factory() as sess:
        proj = _add_project(sess)
        enqueue_if_eligible(sess, settings, proj.id, 85)
        sess.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(process_next_pending_job, factory, settings)
        f2 = pool.submit(process_next_pending_job, factory, settings)
        r1, r2 = f1.result(), f2.result()

    non_none = [r for r in (r1, r2) if r is not None]
    assert len(non_none) == 1
    with factory() as sess:
        assert sess.query(ProposalDraft).count() == 1


def test_telegram_failure_keeps_draft_and_does_not_recreate(
    auto_env: tuple[sessionmaker[Session], Settings, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, settings, _ = auto_env
    monkeypatch.setattr(TechnicalProposalService, "_call_openai_full", _fake_sections)
    monkeypatch.setattr(
        TelegramNotifier,
        "_send_message",
        MagicMock(side_effect=TelegramError("Telegram down")),
    )

    with factory() as sess:
        proj = _add_project(sess)
        enqueue_if_eligible(sess, settings, proj.id, 75)
        sess.commit()

    results = process_pending_jobs(factory, settings)
    assert len(results) == 1
    assert results[0]["status"] == "generated"
    assert results[0]["telegram_notified"] is False

    # Re-running drain must NOT recreate the draft
    assert process_pending_jobs(factory, settings) == []
    with factory() as sess:
        assert sess.query(ProposalDraft).count() == 1


def test_ai_failure_retries_and_does_not_block_other_projects(
    auto_env: tuple[sessionmaker[Session], Settings, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, settings, _ = auto_env
    monkeypatch.setattr(
        TelegramNotifier,
        "_send_message",
        lambda *a, **kw: {"message_id": 13, "ok": True},
    )

    call_count = 0

    def flaky_ai(
        self: TechnicalProposalService, project: Project, *_args: object
    ) -> ProposalSections:
        nonlocal call_count
        call_count += 1
        if "فاشل" in project.title:
            raise AIError("OpenAI rate limit")
        return _fake_sections()

    monkeypatch.setattr(TechnicalProposalService, "_call_openai_full", flaky_ai)

    with factory() as sess:
        p_bad = _add_project(sess, title="مشروع فاشل مؤقتًا")
        p_good = _add_project(sess, title="مشروع ناجح")
        enqueue_if_eligible(sess, settings, p_bad.id, 80)
        enqueue_if_eligible(sess, settings, p_good.id, 80)
        sess.commit()

    results = process_pending_jobs(factory, settings, limit=2)
    assert results[0]["status"] == "failed"
    assert results[1]["status"] == "generated"

    with factory() as sess:
        bad_job = sess.query(AutoProposalJob).filter_by(project_id=p_bad.id).first()
        assert bad_job is not None
        assert bad_job.status == AutoProposalJobStatus.failed
        assert bad_job.attempts == 1
        assert "OpenAI rate limit" in (bad_job.last_error or "")


def test_unlinked_telegram_user_and_ownership_isolation(
    auto_env: tuple[sessionmaker[Session], Settings, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory, settings, _ = auto_env
    settings_no_tg = settings.model_copy(update={"enable_telegram": False})
    monkeypatch.setattr(TechnicalProposalService, "_call_openai_full", _fake_sections)

    with factory() as sess:
        proj = _add_project(sess)
        enqueue_if_eligible(sess, settings_no_tg, proj.id, 85)
        sess.commit()

    results = process_pending_jobs(factory, settings_no_tg)
    assert len(results) == 1
    assert results[0]["status"] == "generated"
    assert results[0]["telegram_notified"] is False

    with factory() as sess:
        other = sess.query(TelegramUser).filter_by(telegram_chat_id=888002).first()
        assert other is not None
        svc = TechnicalProposalService(sess, settings_no_tg)
        with pytest.raises(PermissionError):
            svc.get_proposal(results[0]["draft_id"], other.id)
