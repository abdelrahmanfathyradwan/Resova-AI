"""AI-powered project analysis using OpenAI API.

Scores projects 0–100 against the client profile. Handles API failures, rate limits,
and daily usage caps. Never fabricates data — missing fields are reported as such.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from resova.config.profile import ClientProfile, load_profile
from resova.config.settings import Settings
from resova.db.models import ScoreBand
from resova.utils.errors import AIError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1.0"

# System prompt for the AI scorer
SYSTEM_PROMPT = """\
You are a freelance opportunity evaluator. You analyze project descriptions and score
them 0–100 based on how well they match a freelancer's profile.

Rules:
- Score 0–100 where higher means better match.
- Only use confirmed skills from the profile. Never assume skills not listed.
- Missing data should be noted as "not provided", never invented.
- Competition level (offers/comments) should only be mentioned if explicitly provided.
- Budget is NEVER a reason to exclude a project.
- Respond in valid JSON only.
"""


def _build_user_prompt(
    title: str,
    description: str | None,
    platform: str,
    url: str | None,
    category: str | None,
    profile: ClientProfile,
) -> str:
    """Build the user prompt for AI analysis."""
    profile_summary = []
    for track in profile.tracks:
        keywords = track.match_keywords
        if keywords:
            profile_summary.append(
                f"- {track.label} ({track.experience_years or '?'} years): "
                f"{', '.join(keywords[:10])}"
            )

    excluded = profile.settings.excluded_keywords
    out_of_scope = profile.active_out_of_scope_domains

    return f"""\
Analyze this freelance project opportunity:

**Title:** {title}
**Platform:** {platform}
**Category:** {category or "not provided"}
**Description:** {description or "not provided"}
**URL:** {url or "not provided"}

**Freelancer Profile:**
{chr(10).join(profile_summary) if profile_summary else "No tracks defined"}

**Excluded keywords:** {", ".join(excluded) if excluded else "none"}
**Out-of-scope domains (soft penalty):** {", ".join(out_of_scope) if out_of_scope else "none"}
**Minimum match score:** {profile.settings.min_match_score}

Respond with ONLY valid JSON in this exact format:
{{
  "score": <0-100>,
  "band": "<strong|good|weak|below>",
  "primary_track": "<track_id or null>",
  "matching_skills": ["skill1", "skill2"],
  "missing_skills": ["skill1"],
  "summary": "<2-3 sentence summary>",
  "rationale": "<why this score>",
  "risks": ["risk1", "risk2"],
  "language": "<ar|en>",
  "recommendation": "<notify|skip>"
}}
"""


@dataclass(frozen=True, slots=True)
class AnalysisResult:
    """Result of an AI analysis of a project opportunity."""

    score: int
    band: ScoreBand
    primary_track: str | None
    matching_skills: list[str]
    missing_skills: list[str]
    summary: str
    rationale: str
    risks: list[str]
    language: str
    recommendation: str
    model: str
    prompt_version: str = PROMPT_VERSION
    raw_response: dict[str, Any] = field(default_factory=dict)


def _score_to_band(score: int, profile: ClientProfile) -> ScoreBand:
    """Convert a numeric score to a band using profile thresholds."""
    bands = profile.settings.score_bands
    if score >= bands.strong:
        return ScoreBand.strong
    if score >= bands.good:
        return ScoreBand.good
    if score >= bands.weak:
        return ScoreBand.weak
    return ScoreBand.below


class AIAnalyzer:
    """Analyzes projects using OpenAI API against the client profile.

    Respects daily analysis limits and handles API errors gracefully.
    The analyzer never makes network calls during ``__init__``; calls happen
    only when :meth:`analyze` is invoked.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._daily_count: int = 0
        self._last_reset: datetime = datetime.now(UTC).replace(tzinfo=None)
        self._profile: ClientProfile | None = None

    @property
    def profile(self) -> ClientProfile:
        """Lazily load the client profile."""
        if self._profile is None:
            self._profile = load_profile(self._settings.profile_path)
        return self._profile

    def _reset_daily_counter_if_needed(self) -> None:
        """Reset the daily counter at midnight UTC."""
        now = datetime.now(UTC).replace(tzinfo=None)
        if now.date() > self._last_reset.date():
            self._daily_count = 0
            self._last_reset = now

    def can_analyze(self) -> bool:
        """Check whether analysis is possible (enabled, has key, under limit)."""
        if not self._settings.enable_ai_analysis:
            return False
        if self._settings.openai_api_key is None:
            return False
        self._reset_daily_counter_if_needed()
        return self._daily_count < self._settings.daily_ai_analysis_limit

    def analyze(
        self,
        title: str,
        description: str | None = None,
        platform: str = "unknown",
        url: str | None = None,
        category: str | None = None,
    ) -> AnalysisResult:
        """Analyze a project and return a scored result.

        Raises:
            AIError: When the API call fails or the response cannot be parsed.
            AIError: When the daily limit is exceeded.
        """
        if not self._settings.enable_ai_analysis:
            raise AIError("AI analysis is disabled")

        api_key = self._settings.openai_api_key
        if api_key is None:
            raise AIError("OpenAI API key is not configured")

        self._reset_daily_counter_if_needed()
        if self._daily_count >= self._settings.daily_ai_analysis_limit:
            raise AIError(
                f"Daily AI analysis limit reached ({self._settings.daily_ai_analysis_limit})"
            )

        profile = self.profile
        model = self._settings.openai_model

        user_prompt = _build_user_prompt(
            title=title,
            description=description,
            platform=platform,
            url=url,
            category=category,
            profile=profile,
        )

        try:
            import openai  # noqa: PLC0415

            client = openai.OpenAI(api_key=api_key.get_secret_value())
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.3,
                max_tokens=1024,
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content or "{}"
            parsed = json.loads(content)
        except ImportError:
            raise AIError(
                "openai package is not installed. Install it with: pip install openai"
            ) from None
        except json.JSONDecodeError as exc:
            raise AIError(f"Failed to parse AI response as JSON: {exc}") from exc
        except Exception as exc:
            raise AIError(f"OpenAI API call failed: {exc}") from exc

        self._daily_count += 1

        score = max(0, min(100, int(parsed.get("score", 0))))
        band = _score_to_band(score, profile)

        return AnalysisResult(
            score=score,
            band=band,
            primary_track=parsed.get("primary_track"),
            matching_skills=parsed.get("matching_skills", []),
            missing_skills=parsed.get("missing_skills", []),
            summary=parsed.get("summary", ""),
            rationale=parsed.get("rationale", ""),
            risks=parsed.get("risks", []),
            language=parsed.get("language", "ar"),
            recommendation=parsed.get("recommendation", "skip"),
            model=model,
            raw_response=parsed,
        )

    def analyze_without_api(
        self,
        title: str,
        description: str | None = None,
        platform: str = "unknown",
        category: str | None = None,
    ) -> AnalysisResult:
        """Quick keyword-based scoring without calling the AI API.

        Used as a fallback when AI is disabled or the daily limit is reached.
        """
        profile = self.profile
        score = 0
        matching_skills: list[str] = []
        primary_track: str | None = None

        title_lower = (title or "").lower()
        desc_lower = (description or "").lower()
        combined = f"{title_lower} {desc_lower}"

        for track in profile.tracks:
            track_score = 0
            for keyword in track.match_keywords:
                kw_lower = keyword.lower()
                if kw_lower in combined:
                    track_score += 15
                    matching_skills.append(keyword)
            if track_score > score:
                score = track_score
                primary_track = track.id

        # Clamp score
        score = max(0, min(100, score))

        # Apply excluded keywords penalty
        for excluded in profile.settings.excluded_keywords:
            if excluded.lower() in combined:
                score = max(0, score - 30)

        band = _score_to_band(score, profile)

        count_matched = len(matching_skills)
        return AnalysisResult(
            score=score,
            band=band,
            primary_track=primary_track,
            matching_skills=matching_skills,
            missing_skills=[],
            summary=f"Keyword scoring (AI disabled): {count_matched} keyword(s) matched.",
            rationale="Scored by keyword matching against profile tracks.",
            risks=[],
            language="ar",
            recommendation="notify" if score >= profile.settings.min_match_score else "skip",
            model="keyword-scorer",
        )
