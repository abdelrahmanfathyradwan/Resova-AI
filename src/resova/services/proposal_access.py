"""Direct secure access token generation, verification, and rate limiting."""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict, deque
from datetime import timedelta

from sqlalchemy.orm import Session

from resova.config.settings import Settings
from resova.db.base import utcnow
from resova.db.models import (
    ProposalAccessToken,
    ProposalDraft,
    TelegramUser,
    TelegramUserStatus,
)


def hash_access_token(raw_token: str) -> str:
    """Return the SHA-256 hex digest of a raw access token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_proposal_access_token(
    session: Session,
    *,
    proposal_id: int,
    owner_user_id: int,
    settings: Settings,
) -> tuple[str, ProposalAccessToken]:
    """Create and flush a new hashed, time-limited proposal access token."""
    raw_token = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(days=settings.proposal_access_token_ttl_days)
    record = ProposalAccessToken(
        token_hash=hash_access_token(raw_token),
        proposal_id=proposal_id,
        owner_user_id=owner_user_id,
        expires_at=expires_at,
        revoked=False,
    )
    session.add(record)
    session.flush()
    return raw_token, record


def verify_proposal_access_token(
    session: Session,
    raw_token: str,
) -> tuple[ProposalAccessToken, ProposalDraft, TelegramUser] | None:
    """Validate a raw access token against DB state, expiry, and ownership."""
    if not raw_token or len(raw_token) < 16 or len(raw_token) > 256:
        return None

    token_hash = hash_access_token(raw_token)
    record = (
        session.query(ProposalAccessToken)
        .filter_by(token_hash=token_hash)
        .first()
    )
    if record is None or record.revoked:
        return None
    if record.expires_at <= utcnow():
        return None

    draft = session.query(ProposalDraft).filter_by(id=record.proposal_id).first()
    if draft is None or draft.owner_user_id != record.owner_user_id:
        return None

    user = session.query(TelegramUser).filter_by(id=record.owner_user_id).first()
    if user is None or user.status != TelegramUserStatus.active:
        return None

    record.used_at = utcnow()
    session.flush()
    return record, draft, user


def revoke_proposal_access_tokens(
    session: Session,
    *,
    proposal_id: int | None = None,
    owner_user_id: int | None = None,
) -> int:
    """Revoke matching active proposal access tokens and return count updated."""
    query = session.query(ProposalAccessToken).filter_by(revoked=False)
    if proposal_id is not None:
        query = query.filter_by(proposal_id=proposal_id)
    if owner_user_id is not None:
        query = query.filter_by(owner_user_id=owner_user_id)
    count = query.update({"revoked": True}, synchronize_session=False)
    session.flush()
    return int(count)


class SlidingWindowRateLimiter:
    """Simple in-memory sliding-window rate limiter keyed by client IP."""

    def __init__(self, max_requests: int = 30, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        bucket = self._hits[key]
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        return True

    def reset(self) -> None:
        self._hits.clear()


proposal_access_rate_limiter = SlidingWindowRateLimiter(
    max_requests=30, window_seconds=60
)
