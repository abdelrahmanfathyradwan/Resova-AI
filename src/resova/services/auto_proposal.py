"""Automatic Technical Proposal Generation service and background worker.

Enqueues idempotent jobs for matching Mostaql projects (>= min_match_score)
and processes them asynchronously without blocking ListingPoller.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from resova.config.profile import load_profile
from resova.config.settings import Settings
from resova.db.base import utcnow
from resova.db.models import (
    AutoProposalJob,
    AutoProposalJobStatus,
    Platform,
    Project,
    TelegramUser,
    TelegramUserRole,
    TelegramUserStatus,
)
from resova.db.repositories import TelegramUserRepository
from resova.services.proposal_access import create_proposal_access_token
from resova.services.technical_proposal import TechnicalProposalService
from resova.telegram.bot import TelegramNotifier
from resova.utils.errors import AIError, TelegramError

logger = logging.getLogger(__name__)


def resolve_auto_proposal_owner(
    session: Session, settings: Settings
) -> TelegramUser | None:
    """Resolve the active owner TelegramUser for automatic proposal ownership."""
    owner = (
        session.query(TelegramUser)
        .filter_by(role=TelegramUserRole.owner, status=TelegramUserStatus.active)
        .order_by(TelegramUser.id.asc())
        .first()
    )
    if owner is not None:
        return owner

    owner_chat_id = settings.owner_chat_id
    if owner_chat_id is not None and owner_chat_id > 0:
        return TelegramUserRepository(session).ensure_owner(owner_chat_id)
    return None


def enqueue_if_eligible(
    session: Session,
    settings: Settings,
    project_id: int,
    score: int | None,
) -> AutoProposalJob | None:
    """Enqueue an idempotent AutoProposalJob if the Mostaql project passes matching."""
    project = session.query(Project).filter_by(id=project_id).first()
    if project is None or project.platform != Platform.mostaql:
        return None

    profile = load_profile(settings.profile_path)
    min_score = profile.settings.min_match_score
    if score is None or score < min_score:
        return None

    owner = resolve_auto_proposal_owner(session, settings)
    if owner is None:
        logger.warning(
            "AutoProposal: no active owner user found for project %d; skipping.",
            project_id,
        )
        return None

    existing = (
        session.query(AutoProposalJob)
        .filter_by(project_id=project.id, owner_user_id=owner.id)
        .first()
    )
    if existing is not None:
        return existing

    job = AutoProposalJob(
        project_id=project.id,
        owner_user_id=owner.id,
        status=AutoProposalJobStatus.pending,
        attempts=0,
        max_attempts=3,
    )
    try:
        with session.begin_nested():
            session.add(job)
            session.flush()
    except IntegrityError:
        return (
            session.query(AutoProposalJob)
            .filter_by(project_id=project.id, owner_user_id=owner.id)
            .first()
        )
    return job


def _claim_next_job(session: Session) -> AutoProposalJob | None:
    """Atomically claim the next pending or retryable failed job."""
    candidates = session.scalars(
        select(AutoProposalJob)
        .where(
            AutoProposalJob.status.in_(
                [AutoProposalJobStatus.pending, AutoProposalJobStatus.failed]
            ),
            AutoProposalJob.attempts < AutoProposalJob.max_attempts,
        )
        .order_by(AutoProposalJob.attempts.asc(), AutoProposalJob.id.asc())
        .limit(10)
    ).all()

    for candidate in candidates:
        res = session.execute(
            update(AutoProposalJob)
            .where(
                AutoProposalJob.id == candidate.id,
                AutoProposalJob.status.in_(
                    [AutoProposalJobStatus.pending, AutoProposalJobStatus.failed]
                ),
                AutoProposalJob.attempts < AutoProposalJob.max_attempts,
            )
            .values(
                status=AutoProposalJobStatus.processing,
                attempts=AutoProposalJob.attempts + 1,
                started_at=utcnow(),
                last_error=None,
            )
        )
        if res.rowcount == 1:
            session.commit()
            session.refresh(candidate)
            return candidate
        session.rollback()
    return None


def process_next_pending_job(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    notifier: TelegramNotifier | None = None,
) -> dict[str, Any] | None:
    """Claim and process a single AutoProposalJob. Safe for concurrent workers."""
    with session_factory() as session:
        job = _claim_next_job(session)
        if job is None:
            return None

        job_id = job.id
        project_id = job.project_id
        owner_user_id = job.owner_user_id

        try:
            svc = TechnicalProposalService(session, settings)
            draft = svc.generate_technical_proposal(
                project_id=project_id,
                owner_user_id=owner_user_id,
                require_ai=True,
                language="ar",
            )
            job.draft_id = draft.id
            job.status = AutoProposalJobStatus.generated
            job.last_error = None
            job.finished_at = utcnow()
            session.commit()
            session.refresh(draft)
        except (AIError, Exception) as exc:
            session.rollback()
            err_msg = str(exc)[:1000]
            logger.warning(
                "AutoProposal job %d (project %d) failed: %s",
                job_id,
                project_id,
                err_msg,
            )
            failed_job = session.query(AutoProposalJob).filter_by(id=job_id).first()
            if failed_job is not None:
                failed_job.status = AutoProposalJobStatus.failed
                failed_job.last_error = err_msg
                failed_job.finished_at = utcnow()
                session.commit()
            return {
                "job_id": job_id,
                "project_id": project_id,
                "status": AutoProposalJobStatus.failed.value,
                "error": err_msg,
            }

        # Notify owner via Telegram AFTER draft is committed
        telegram_notified = False
        owner = session.query(TelegramUser).filter_by(id=owner_user_id).first()
        project = session.query(Project).filter_by(id=project_id).first()
        chat_id = owner.telegram_chat_id if owner is not None else None

        if (
            chat_id is not None
            and chat_id > 0
            and settings.enable_telegram
            and settings.telegram_bot_token is not None
            and not job.telegram_notified
        ):
            base_url = settings.public_base_url.rstrip("/")
            raw_token, _ = create_proposal_access_token(
                session,
                proposal_id=draft.id,
                owner_user_id=owner_user_id,
                settings=settings,
            )
            session.commit()
            proposal_url = f"{base_url}/proposal-access/{raw_token}"
            project_title = project.title if project else f"مشروع #{project_id}"
            proposal_title = draft.title or f"عرض فني v{draft.version}"
            try:
                tg = notifier or TelegramNotifier(settings)
                tg.send_proposal_created_notification(
                    chat_id=chat_id,
                    draft_id=draft.id,
                    project_title=project_title,
                    proposal_title=proposal_title,
                    proposal_url=proposal_url,
                )
                job.telegram_notified = True
                session.commit()
                telegram_notified = True
            except (TelegramError, Exception) as exc:
                logger.warning(
                    "AutoProposal job %d saved draft %d but Telegram notification failed: %s",
                    job_id,
                    draft.id,
                    exc,
                )

        return {
            "job_id": job_id,
            "project_id": project_id,
            "draft_id": draft.id,
            "status": AutoProposalJobStatus.generated.value,
            "telegram_notified": telegram_notified,
        }


def process_pending_jobs(
    session_factory: sessionmaker[Session],
    settings: Settings,
    *,
    limit: int = 10,
    notifier: TelegramNotifier | None = None,
) -> list[dict[str, Any]]:
    """Drain up to ``limit`` pending/retryable auto-proposal jobs."""
    results: list[dict[str, Any]] = []
    for _ in range(limit):
        res = process_next_pending_job(session_factory, settings, notifier=notifier)
        if res is None:
            break
        results.append(res)
    return results
