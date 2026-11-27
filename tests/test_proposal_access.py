"""Tests for Direct Secure Access tokens (/proposal-access/{token})."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from resova.api.app import create_app
from resova.api.dashboard import compute_access_code
from resova.config.settings import Settings
from resova.db.base import Base, utcnow
from resova.db.models import (
    Platform,
    Project,
    ProjectAnalysis,
    ProposalAccessToken,
    ScoreBand,
    SourceKind,
    TelegramUser,
    TelegramUserRole,
    TelegramUserStatus,
)
from resova.db.session import create_db_engine, create_session_factory
from resova.services.auto_proposal import enqueue_if_eligible, process_next_pending_job
from resova.services.proposal_access import (
    create_proposal_access_token,
    hash_access_token,
    proposal_access_rate_limiter,
    revoke_proposal_access_tokens,
)
from resova.services.technical_proposal import TechnicalProposalService


@pytest.fixture(autouse=True)
def _reset_rate_limiter() -> Iterator[None]:
    proposal_access_rate_limiter.reset()
    yield
    proposal_access_rate_limiter.reset()


@pytest.fixture()
def access_env(
    tmp_path: Path,
) -> Iterator[tuple[TestClient, sessionmaker[Session], Settings]]:
    db_file = tmp_path / "access_test.db"
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite:///{db_file}",
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=True,
        telegram_bot_token="123456:TEST_BOT_TOKEN_FOR_DIRECT_ACCESS",
        telegram_allowed_user_id=990001,
        public_base_url="https://resova-production.up.railway.app",
    )
    engine = create_db_engine(settings.database_url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, factory, settings
    engine.dispose()


def _seed_user_and_two_proposals(
    factory: sessionmaker[Session], settings: Settings
) -> tuple[TelegramUser, TelegramUser, int, int, int]:
    with factory() as s:
        u1 = TelegramUser(
            telegram_chat_id=990001,
            username="owner1",
            first_name="أحمد",
            role=TelegramUserRole.owner,
            status=TelegramUserStatus.active,
        )
        u2 = TelegramUser(
            telegram_chat_id=990002,
            username="user2",
            first_name="سارة",
            role=TelegramUserRole.user,
            status=TelegramUserStatus.active,
        )
        s.add_all([u1, u2])
        s.flush()

        p1 = Project(
            platform=Platform.mostaql,
            source_kind=SourceKind.alert_email,
            title="مشروع تصميم نظام ري حديث",
            description="مطلوب تصميم مخططات وشبكة ري حديثة.",
            url="https://mostaql.com/projects/999001",
            external_id="999001",
        )
        p2 = Project(
            platform=Platform.mostaql,
            source_kind=SourceKind.alert_email,
            title="مشروع أعمال كهروميكانيك",
            description="مطلوب إعداد رسومات تنفيذية MEP.",
            url="https://mostaql.com/projects/999002",
            external_id="999002",
        )
        s.add_all([p1, p2])
        s.flush()

        svc = TechnicalProposalService(s, settings)
        d1 = svc.generate_technical_proposal(p1.id, u1.id, require_ai=False)
        d2 = svc.generate_technical_proposal(p2.id, u1.id, require_ai=False)
        d3 = svc.generate_technical_proposal(p2.id, u2.id, require_ai=False)
        s.commit()
        return u1, u2, d1.id, d2.id, d3.id


def test_direct_access_opens_proposal_without_login(
    access_env: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    client, factory, settings = access_env
    u1, _, d1_id, _, _ = _seed_user_and_two_proposals(factory, settings)

    with factory() as s:
        raw_token, rec = create_proposal_access_token(
            s, proposal_id=d1_id, owner_user_id=u1.id, settings=settings
        )
        assert rec.token_hash == hash_access_token(raw_token)
        assert rec.token_hash != raw_token
        s.commit()

    # Without following redirects, verify security headers & redirect
    r0 = client.get(f"/proposal-access/{raw_token}", follow_redirects=False)
    assert r0.status_code == 303
    assert r0.headers["location"] == f"/dashboard/proposals/{d1_id}"
    assert r0.headers.get("referrer-policy") == "no-referrer"
    assert r0.headers.get("cache-control") == "no-store"

    # Following redirects lands directly on the proposal editor page (no login form)
    r1 = client.get(f"/proposal-access/{raw_token}", follow_redirects=True)
    assert r1.status_code == 200
    assert "محرر العرض الفني" in r1.text
    assert "تسجيل الدخول إلى لوحة تحكم Resova" not in r1.text


def test_token_is_scoped_to_bound_proposal_and_blocks_other_pages(
    access_env: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    client, factory, settings = access_env
    u1, _, d1_id, d2_id, d3_id = _seed_user_and_two_proposals(factory, settings)

    with factory() as s:
        raw_token, _ = create_proposal_access_token(
            s, proposal_id=d1_id, owner_user_id=u1.id, settings=settings
        )
        s.commit()

    r = client.get(f"/proposal-access/{raw_token}", follow_redirects=True)
    assert r.status_code == 200

    # Changing proposal_id to another proposal owned by u1 or u2 must be rejected
    r_other_own = client.get(f"/dashboard/proposals/{d2_id}", follow_redirects=False)
    assert r_other_own.status_code == 403

    r_other_user = client.get(f"/dashboard/proposals/{d3_id}", follow_redirects=False)
    assert r_other_user.status_code == 403

    r_api_other = client.get(f"/api/proposals/{d2_id}")
    assert r_api_other.status_code == 403

    # Scoped session cannot access general dashboard pages
    r_dash = client.get("/dashboard", follow_redirects=False)
    assert r_dash.status_code == 303
    assert r_dash.headers["location"] == "/dashboard/login"

    r_prof = client.get("/api/profile/professional")
    assert r_prof.status_code == 401


def test_pdf_and_save_work_via_token_session(
    access_env: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    client, factory, settings = access_env
    u1, _, d1_id, _, _ = _seed_user_and_two_proposals(factory, settings)

    with factory() as s:
        raw_token, _ = create_proposal_access_token(
            s, proposal_id=d1_id, owner_user_id=u1.id, settings=settings
        )
        s.commit()

    assert client.get(f"/proposal-access/{raw_token}", follow_redirects=True).status_code == 200

    pdf_res = client.get(f"/api/proposals/{d1_id}/pdf")
    assert pdf_res.status_code == 200
    assert pdf_res.headers["content-type"] == "application/pdf"
    assert pdf_res.content.startswith(b"%PDF-")

    cur = client.get(f"/api/proposals/{d1_id}").json()
    sections = cur["sections"]
    sections["intro"] = "مقدمة محدثة عبر رابط الوصول المباشر"
    put_res = client.put(
        f"/api/proposals/{d1_id}",
        json={
            "title": "عنوان محدث",
            "sections": sections,
            "timeline_text": sections["timeline"],
            "cost_text": sections["cost"],
            "editor_status": "edited",
        },
    )
    assert put_res.status_code == 200
    assert put_res.json()["title"] == "عنوان محدث"


def test_expired_revoked_and_invalid_tokens_are_rejected(
    access_env: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    client, factory, settings = access_env
    u1, _, d1_id, _, _ = _seed_user_and_two_proposals(factory, settings)

    with factory() as s:
        tok_exp, rec_exp = create_proposal_access_token(
            s, proposal_id=d1_id, owner_user_id=u1.id, settings=settings
        )
        rec_exp.expires_at = utcnow() - timedelta(minutes=5)

        tok_rev, _ = create_proposal_access_token(
            s, proposal_id=d1_id, owner_user_id=u1.id, settings=settings
        )
        s.flush()
        revoked_count = revoke_proposal_access_tokens(s, proposal_id=d1_id)
        assert revoked_count >= 1
        s.commit()

    for bad in (tok_exp, tok_rev, "nonexistent-random-token-1234567890"):
        res = client.get(f"/proposal-access/{bad}", follow_redirects=False)
        assert res.status_code == 403
        assert res.headers.get("referrer-policy") == "no-referrer"
        assert "غير صالح" in res.text


def test_rate_limiting_on_proposal_access(
    access_env: tuple[TestClient, sessionmaker[Session], Settings],
) -> None:
    client, _, _ = access_env
    for _ in range(30):
        r = client.get("/proposal-access/invalid-token-0000000000", follow_redirects=False)
        assert r.status_code == 403
    r_limited = client.get("/proposal-access/invalid-token-0000000000", follow_redirects=False)
    assert r_limited.status_code == 429


def test_notifications_use_direct_proposal_access_urls(
    access_env: tuple[TestClient, sessionmaker[Session], Settings],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, factory, settings = access_env
    u1, _, _, _, _ = _seed_user_and_two_proposals(factory, settings)

    with factory() as s:
        proj = Project(
            platform=Platform.mostaql,
            source_kind=SourceKind.alert_email,
            title="مشروع تصميم شبكة مياه",
            description="تصميم شبكة مياه وصرف صحي.",
            url="https://mostaql.com/projects/999003",
            external_id="999003",
        )
        proj2 = Project(
            platform=Platform.mostaql,
            source_kind=SourceKind.alert_email,
            title="مشروع تصميم أنظمة تكييف",
            description="تصميم مخططات التكييف المركزي.",
            url="https://mostaql.com/projects/999004",
            external_id="999004",
        )
        s.add_all([proj, proj2])
        s.flush()
        s.add_all([
            ProjectAnalysis(
                project_id=proj.id,
                score=85,
                band=ScoreBand.strong,
                track="MEP / Infrastructure",
                model="test",
            ),
            ProjectAnalysis(
                project_id=proj2.id,
                score=88,
                band=ScoreBand.strong,
                track="MEP / Infrastructure",
                model="test",
            ),
        ])
        s.commit()
        proj_id = proj.id
        proj2_id = proj2.id

    # 1. Manual generation API sends /proposal-access/<token>
    sent_urls: list[str] = []

    def fake_send(self, *, chat_id, draft_id, project_title, proposal_title, proposal_url):
        sent_urls.append(proposal_url)
        return 1

    monkeypatch.setattr(
        "resova.api.dashboard.TelegramNotifier.send_proposal_created_notification",
        fake_send,
    )

    code = compute_access_code(u1.telegram_chat_id, settings)
    login_res = client.post(
        "/dashboard/login",
        data={"chat_id": str(u1.telegram_chat_id), "access_code": code},
        follow_redirects=False,
    )
    assert login_res.status_code == 303

    gen_res = client.post(
        f"/api/projects/{proj_id}/technical-proposals",
        json={"require_ai": False, "language": "ar"},
    )
    assert gen_res.status_code == 201
    assert gen_res.json()["telegram_notified"] is True
    assert len(sent_urls) == 1
    assert "/proposal-access/" in sent_urls[0]
    assert "/dashboard/proposals/" not in sent_urls[0]

    # 2. AutoProposal sends /proposal-access/<token>
    orig_gen = TechnicalProposalService.generate_technical_proposal
    monkeypatch.setattr(
        TechnicalProposalService,
        "generate_technical_proposal",
        lambda self, project_id, owner_user_id, *, require_ai=True, language="ar": orig_gen(
            self,
            project_id,
            owner_user_id,
            require_ai=False,
            language=language,
        ),
    )
    mock_notifier = MagicMock()
    with factory() as s:
        job = enqueue_if_eligible(s, settings, proj2_id, score=88)
        assert job is not None
        s.commit()

    res = process_next_pending_job(factory, settings, notifier=mock_notifier)
    assert res is not None
    assert res["telegram_notified"] is True
    call_kwargs = mock_notifier.send_proposal_created_notification.call_args.kwargs
    assert "/proposal-access/" in call_kwargs["proposal_url"]
    assert "/dashboard/proposals/" not in call_kwargs["proposal_url"]

    # Verify tokens exist in DB as hashes
    with factory() as s:
        assert s.query(ProposalAccessToken).count() >= 2
