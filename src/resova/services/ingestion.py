"""Ingestion service: source → normalize → dedup → score → notify.

Orchestrates the full pipeline from raw project discovery through to Telegram
notification. Each step is independently testable.
"""

from __future__ import annotations

import logging
from typing import Any

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
    ProjectAnalysis,
    ScoreBand,
    SourceKind,
)
from resova.db.repositories import ProjectRepository, ProjectUpsert, UpsertOutcome
from resova.telegram.bot import TelegramNotifier

logger = logging.getLogger(__name__)


class IngestionService:
    """End-to-end ingestion: receive a project, deduplicate, analyze, notify.

    This is the central orchestrator — sources feed into it, and it drives
    the AI and Telegram layers.
    """

    def __init__(
        self,
        session: Session,
        settings: Settings,
        *,
        analyzer: AIAnalyzer | None = None,
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._repo = ProjectRepository(session)
        self._analyzer = analyzer or AIAnalyzer(settings)
        self._notifier = notifier or TelegramNotifier(settings)
        self._profile: ClientProfile | None = None

    @property
    def profile(self) -> ClientProfile:
        if self._profile is None:
            self._profile = load_profile(self._settings.profile_path)
        return self._profile

    def ingest_project(self, data: ProjectUpsert) -> dict[str, Any]:
        """Process a single project through the full pipeline.

        Returns a dict describing what happened at each stage.
        """
        result: dict[str, Any] = {
            "project_id": None,
            "outcome": None,
            "analysis": None,
            "notification": None,
            "errors": [],
        }

        # 1. Upsert (dedup)
        try:
            upsert = self._repo.upsert_project(data)
            self._session.flush()
            result["project_id"] = upsert.project.id
            result["outcome"] = upsert.outcome.value
        except Exception as exc:
            logger.error("Failed to upsert project: %s", exc)
            result["errors"].append(f"upsert: {exc}")
            return result

        project = upsert.project

        # 2. AI Analysis (only for new projects)
        if upsert.outcome == UpsertOutcome.created:
            analysis = self._analyze_project(project)
            if analysis is not None:
                result["analysis"] = {
                    "score": analysis.score,
                    "band": analysis.band.value
                    if isinstance(analysis.band, ScoreBand)
                    else analysis.band,
                    "summary": analysis.summary,
                }

                # 3. Save analysis to DB
                try:
                    db_analysis = ProjectAnalysis(
                        project_id=project.id,
                        track=analysis.primary_track,
                        score=analysis.score,
                        band=analysis.band,
                        summary=analysis.summary,
                        rationale=analysis.rationale,
                        model=analysis.model,
                        prompt_version=analysis.prompt_version,
                        analyzed_at=utcnow(),
                    )
                    self._session.add(db_analysis)
                    self._session.flush()
                except Exception as exc:
                    logger.error("Failed to save analysis: %s", exc)
                    result["errors"].append(f"save_analysis: {exc}")

                # 4. Notify if score meets threshold
                if analysis.score >= self.profile.settings.min_match_score:
                    notif_result = self._notify(project, analysis)
                    result["notification"] = notif_result

        return result

    def _analyze_project(self, project: Project) -> AnalysisResult | None:
        """Run AI analysis on a project. Returns None on failure."""
        try:
            if self._analyzer.can_analyze():
                return self._analyzer.analyze(
                    title=project.title,
                    description=project.description,
                    platform=project.platform.value,
                    url=project.url,
                    category=project.category,
                )
            else:
                # Fall back to keyword scoring
                return self._analyzer.analyze_without_api(
                    title=project.title,
                    description=project.description,
                    platform=project.platform.value,
                    category=project.category,
                )
        except Exception as exc:
            logger.error("Analysis failed for project %d: %s", project.id, exc)
            return None

    def _notify(self, project: Project, analysis: AnalysisResult) -> dict[str, Any]:
        """Send a Telegram notification for a scored project."""
        if not self._notifier.enabled:
            return {"status": "skipped", "reason": "telegram_disabled"}

        try:
            send_result = self._notifier.send_opportunity(
                project_id=project.id,
                title=project.title,
                platform=project.platform.value,
                url=project.url,
                score=analysis.score,
                band=analysis.band.value
                if isinstance(analysis.band, ScoreBand)
                else str(analysis.band),
                summary=analysis.summary,
                language=analysis.language,
            )

            # Record notification in DB
            notification = Notification(
                project_id=project.id,
                channel="telegram",
                status=send_result["status"],
                score=analysis.score,
                telegram_message_id=send_result.get("message_id"),
                sent_at=utcnow() if send_result["status"] == NotificationStatus.sent else None,
                error=send_result.get("error"),
            )
            self._session.add(notification)
            self._session.flush()

            return send_result
        except Exception as exc:
            logger.error("Notification failed for project %d: %s", project.id, exc)
            return {"status": "failed", "error": str(exc)}

    def ingest_manual_paste(
        self,
        title: str,
        description: str | None = None,
        url: str | None = None,
        platform: Platform = Platform.manual,
    ) -> dict[str, Any]:
        """Ingest a manually pasted project."""
        data = ProjectUpsert(
            platform=platform,
            source_kind=SourceKind.manual_paste,
            title=title,
            description=description,
            url=url,
        )
        return self.ingest_project(data)
