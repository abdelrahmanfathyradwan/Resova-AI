"""End-to-end integration test: ingest → AI analysis → DB save → Telegram notification.

This test runs against REAL external services (OpenAI, Telegram) using the credentials
in .env.  It creates an isolated SQLite database to avoid polluting the production DB.

Run explicitly:
    python tests/test_integration_e2e.py
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

# Ensure the src package is importable when run as a standalone script.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> None:  # noqa: C901
    # ── 0. Load real settings from .env ──────────────────────────────────
    # We clear the lru_cache so the Settings singleton picks up .env fresh.
    from resova.config.settings import get_settings

    get_settings.cache_clear()
    settings = get_settings()

    print("=" * 72)
    print("RESOVA — END-TO-END INTEGRATION TEST")
    print("=" * 72)
    print(f"  AI enabled  : {settings.enable_ai_analysis}")
    print(f"  TG enabled  : {settings.enable_telegram}")
    print(f"  OpenAI model: {settings.openai_model}")
    print(f"  TG user id  : {settings.telegram_allowed_user_id}")
    print()

    # ── 1. Create an isolated test database ──────────────────────────────
    tmp_dir = tempfile.mkdtemp(prefix="resova_e2e_")
    test_db_path = str(Path(tmp_dir) / "test_e2e.db")
    test_db_url = f"sqlite:///{test_db_path}"
    print(f"[1] Test DB: {test_db_path}")

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from resova.db.base import Base

    engine = create_engine(test_db_url, echo=False)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    print("    ✔ Schema created (9 tables)")

    # ── 2. Instantiate the pipeline components ───────────────────────────
    from resova.ai.analyzer import AIAnalyzer
    from resova.services.ingestion import IngestionService
    from resova.telegram.bot import TelegramNotifier

    analyzer = AIAnalyzer(settings)
    notifier = TelegramNotifier(settings)
    svc = IngestionService(session, settings, analyzer=analyzer, notifier=notifier)

    print("[2] Pipeline components instantiated")
    print(f"    AIAnalyzer.can_analyze() = {analyzer.can_analyze()}")
    print(f"    TelegramNotifier.enabled = {notifier.enabled}")
    print()

    # ── 3. Ingest a realistic engineering project (Arabic) ───────────────
    print("[3] Ingesting test project …")
    test_title = "مطلوب رسم مخططات مكافحة الحريق AutoCAD"
    test_description = textwrap.dedent("""\
        نحتاج مهندس متخصص في أنظمة الحماية من الحريق لتصميم مخططات
        مكافحة الحريق وإنذار الحريق لمبنى سكني مكون من 10 طوابق.
        يشمل العمل: رسم مخططات الرش الآلي، شبكة الإطفاء، مضخات الحريق،
        والحسابات الهيدروليكية. يجب أن تكون المخططات جاهزة للتقديم
        للدفاع المدني. المطلوب تسليم ملفات DWG وPDF.
    """)
    test_url = "https://mostaql.com/project/999999-fire-protection-test"

    from resova.db.models import Platform

    result = svc.ingest_manual_paste(
        title=test_title,
        description=test_description,
        url=test_url,
        platform=Platform.mostaql,
    )

    print(f"    outcome     : {result['outcome']}")
    print(f"    project_id  : {result['project_id']}")
    print(f"    errors      : {result['errors']}")
    print()

    # ── 4. Check AI analysis result ──────────────────────────────────────
    analysis = result.get("analysis")
    if analysis is None:
        print("[4] ✘ AI analysis returned None — check logs above")
        print(f"    errors: {result['errors']}")
    else:
        print("[4] ✔ AI analysis result:")
        print(f"    score   : {analysis['score']}/100")
        print(f"    band    : {analysis['band']}")
        summary_txt = analysis.get("summary", "")
        if len(summary_txt) > 100:
            summary_txt = f"{summary_txt[:100]}…"
        print(f"    summary : {summary_txt}")
    print()

    # ── 5. Check database records ────────────────────────────────────────
    from resova.db.models import Notification, Project, ProjectAnalysis

    projects = session.query(Project).all()
    analyses = session.query(ProjectAnalysis).all()
    notifications = session.query(Notification).all()

    print("[5] Database records:")
    print(f"    projects       : {len(projects)}")
    print(f"    project_analyses: {len(analyses)}")
    print(f"    notifications  : {len(notifications)}")

    if projects:
        p = projects[0]
        print(f"    → project.id={p.id}, title='{p.title[:60]}…'")
        print(f"      platform={p.platform}, source_kind={p.source_kind}")
        print(
            f"      url_key={'set' if p.url_key else 'null'}, "
            f"content_key={'set' if p.content_key else 'null'}"
        )
    if analyses:
        a = analyses[0]
        print(f"    → analysis.id={a.id}, score={a.score}, band={a.band}")
        print(f"      track={a.track}, model={a.model}")
    if notifications:
        n = notifications[0]
        print(f"    → notification.id={n.id}, status={n.status}")
        print(f"      telegram_message_id={n.telegram_message_id}")
    print()

    # ── 6. Check Telegram notification result ────────────────────────────
    notif = result.get("notification")
    if notif is None:
        min_score = svc.profile.settings.min_match_score
        score_got = analysis["score"] if analysis else "N/A"
        print(f"[6] ⓘ No notification sent (score={score_got}, min={min_score})")
    elif notif.get("status") == "sent" or str(notif.get("status")) == "sent":
        print("[6] ✔ Telegram notification SENT:")
        print(f"    message_id : {notif.get('message_id')}")
        print(f"    status     : {notif.get('status')}")
    else:
        print("[6] ✘ Telegram notification FAILED:")
        print(f"    status : {notif.get('status')}")
        print(f"    error  : {notif.get('error')}")
    print()

    # ── 7. Dedup test: ingest the same project again ─────────────────────
    print("[7] Dedup test: re-ingesting same project …")
    result2 = svc.ingest_manual_paste(
        title=test_title,
        description=test_description,
        url=test_url,
        platform=Platform.mostaql,
    )
    print(f"    outcome : {result2['outcome']}  (expected: 'updated')")
    print(f"    analysis: {result2.get('analysis')}  (expected: None — already scored)")
    # Confirm no new DB rows
    assert session.query(Project).count() == 1, "Dedup failed: more than 1 project"
    assert session.query(ProjectAnalysis).count() == len(analyses), "Dedup: extra analysis"
    print("    ✔ Dedup works — no duplicate rows")
    print()

    # ── 8. Commit and clean up ───────────────────────────────────────────
    session.commit()
    session.close()
    engine.dispose()

    print("=" * 72)
    print("INTEGRATION TEST COMPLETE")
    print("=" * 72)

    # Return exit code based on AI analysis result
    if analysis and analysis.get("score", 0) >= 0:
        print("\n✔ All steps passed.")
        sys.exit(0)
    else:
        print("\n✘ AI analysis did not return a valid result.")
        sys.exit(1)


if __name__ == "__main__":
    main()
