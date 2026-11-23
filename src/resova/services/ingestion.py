"""Ingestion service: source → normalize → dedup → score → notify.

Orchestrates the full pipeline from raw project discovery through to Telegram
notification. Each step is independently testable.
"""

from __future__ import annotations

import enum
import logging
from typing import Any

from sqlalchemy import select
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


class FreshnessClassification(enum.StrEnum):
    fresh_confirmed = "fresh_confirmed"
    freshness_unverified = "freshness_unverified"
    bumped_stale = "bumped_stale"


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
                    "confidence": analysis.confidence,
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
                        confidence=analysis.confidence,
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

                # 4. Check freshness & Notify according to score thresholds
                notif_result = self._process_notification(project, analysis)
                result["notification"] = notif_result

        return result

    def classify_freshness(
        self, project: Project
    ) -> tuple[FreshnessClassification, str]:
        """Classify project freshness into fresh, unverified, or bumped stale."""
        max_age = getattr(self._settings, "max_notification_age_hours", 48)

        # 1. Authoritative date if published_at is present
        if project.published_at is not None:
            age_hours = (utcnow() - project.published_at).total_seconds() / 3600.0
            if age_hours <= max_age:
                return (
                    FreshnessClassification.fresh_confirmed,
                    f"published_within_{round(age_hours, 1)}h",
                )
            return (
                FreshnessClassification.bumped_stale,
                f"published_at_stale_{round(age_hours, 1)}h_exceeds_{max_age}h",
            )

        # 2. published_at is None (Unknown)
        # Check if project has independent reliable proof of being an old topic
        if (project.offers_count and project.offers_count > 3) or (
            project.comments_count and project.comments_count > 3
        ):
            return FreshnessClassification.bumped_stale, "existing_comments_or_offers_signal_age"

        # Without published_at, project freshness cannot be verified
        return (
            FreshnessClassification.freshness_unverified,
            "unverified_publication_date",
        )

    def _process_notification(self, project: Project, analysis: AnalysisResult) -> dict[str, Any]:
        """Process opportunity notification according to score thresholds and freshness."""
        min_score = self.profile.settings.min_match_score

        # Check score threshold first
        if analysis.score < min_score:
            notification = Notification(
                project_id=project.id,
                channel="telegram",
                status=NotificationStatus.skipped,
                score=analysis.score,
                sent_at=None,
                error="below_threshold",
            )
            self._session.add(notification)
            self._session.flush()
            return {"status": "skipped", "reason": "below_threshold"}

        # Classify freshness
        classification, reason = self.classify_freshness(project)

        if classification == FreshnessClassification.bumped_stale:
            error_code = "bumped_stale" if project.last_activity_at else "stale_project_skipped"
            logger.info("Project %d skipped: %s (%s)", project.id, error_code, reason)
            notification = Notification(
                project_id=project.id,
                channel="telegram",
                status=NotificationStatus.skipped,
                score=analysis.score,
                sent_at=None,
                error=error_code,
            )
            self._session.add(notification)
            self._session.flush()
            return {
                "status": "skipped",
                "reason": error_code,
                "classification": FreshnessClassification.bumped_stale.value,
                "details": reason,
            }

        if classification == FreshnessClassification.freshness_unverified:
            logger.info("Project %d skipped: unverified publication date (%s)", project.id, reason)
            notification = Notification(
                project_id=project.id,
                channel="telegram",
                status=NotificationStatus.skipped,
                score=analysis.score,
                sent_at=None,
                error="freshness_unverified",
            )
            self._session.add(notification)
            self._session.flush()
            return {
                "status": "skipped",
                "reason": "freshness_unverified",
                "classification": FreshnessClassification.freshness_unverified.value,
                "details": reason,
            }

        # Route by Score Band (fresh_confirmed only)
        # Strong (>=85) or Good (70-84): Immediate individual notification
        if analysis.score >= 70:
            return self._notify(project, analysis)

        # Moderate (45-69): Queue for digest to prevent Telegram flood
        notification = Notification(
            project_id=project.id,
            channel="telegram",
            status=NotificationStatus.pending_digest,
            score=analysis.score,
            sent_at=None,
            error=None,
        )
        self._session.add(notification)
        self._session.flush()
        return {
            "status": "pending_digest",
            "reason": "queued_for_digest",
            "classification": FreshnessClassification.fresh_confirmed.value,
        }

    def flush_pending_digest(self) -> dict[str, Any]:
        """Send all pending moderate notifications as a single consolidated Telegram digest."""
        stmt = (
            select(Notification, Project, ProjectAnalysis)
            .join(Project, Notification.project_id == Project.id)
            .outerjoin(ProjectAnalysis, ProjectAnalysis.project_id == Project.id)
            .where(Notification.status == NotificationStatus.pending_digest)
            .order_by(Notification.id)
        )
        rows = self._session.execute(stmt).all()
        if not rows:
            return {"status": "empty", "count": 0}

        items = []
        notifs_to_update = []
        for notif, proj, pa in rows:
            items.append({
                "project_id": proj.id,
                "title": proj.title,
                "url": proj.url,
                "platform": proj.platform.value,
                "score": notif.score or (pa.score if pa else 0),
                "track": pa.track if pa else None,
            })
            notifs_to_update.append(notif)

        recipients = self._get_active_recipients()
        res = self._notifier.send_digest(items, recipients=recipients)
        if res.get("status") == NotificationStatus.sent:
            now = utcnow()
            for notif in notifs_to_update:
                notif.status = NotificationStatus.sent
                notif.sent_at = now
                notif.telegram_message_id = res.get("message_id")
            self._session.flush()
        return res

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

    def _get_active_recipients(self) -> list[int]:
        """Return list of chat IDs to notify: all active users + owner."""
        recipients: list[int] = []
        try:
            from resova.db.models import TelegramUser, TelegramUserStatus

            stmt = select(TelegramUser.telegram_chat_id).where(
                TelegramUser.status == TelegramUserStatus.active
            )
            rows = self._session.execute(stmt).scalars().all()
            recipients.extend(rows)
        except Exception as exc:
            logger.warning("Could not query active telegram users: %s", exc)

        owner_id = self._settings.owner_chat_id
        if owner_id is not None:
            try:
                from resova.db.repositories import TelegramUserRepository

                repo = TelegramUserRepository(self._session)
                repo.ensure_owner(owner_id)
            except Exception as exc:
                logger.warning("Could not auto-provision owner: %s", exc)

            if owner_id not in recipients:
                recipients.append(owner_id)

        return recipients

    def _notify(self, project: Project, analysis: AnalysisResult) -> dict[str, Any]:
        """Send a Telegram notification for a scored project."""
        if not self._notifier.enabled:
            return {"status": "skipped", "reason": "telegram_disabled"}

        try:
            recipients = self._get_active_recipients()
            send_result = self._notifier.send_opportunity(
                project_id=project.id,
                title=project.title,
                platform=project.platform.value,
                url=project.url,
                score=analysis.score,
                confidence=analysis.confidence,
                band=analysis.band.value
                if isinstance(analysis.band, ScoreBand)
                else str(analysis.band),
                summary=analysis.summary,
                rationale=analysis.rationale,
                language=analysis.language,
                recipients=recipients,
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
