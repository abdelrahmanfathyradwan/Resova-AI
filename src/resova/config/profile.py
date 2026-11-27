"""Client profile configuration (Phase 1 / Step 2.3).

The profile is an editable YAML document (``config/profile.yaml``) describing the single
freelance engineer Resova works for: target titles, the three skill tracks, keywords,
match thresholds and exclusions. Nothing here is hardcoded in Python.

Two safety rules are encoded in the models:

* Arabic keywords are only *proposed* (``approved: false``) until the client approves
  them; unapproved Arabic keywords are never used for matching.
* Skills the client has not confirmed (``confirmed: false``) are excluded from matching
  and drafting until they are confirmed.

The profile is versioned by an explicit ``version`` number plus a deterministic content
hash, and :meth:`ClientProfile.snapshot` returns a serialisable payload for the future
``user_profile`` table.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TRACK_IDS = ("engineering", "architectural_interior", "presentation_design", "cv_resume")


class ArabicKeywordProposal(BaseModel):
    """A candidate Arabic keyword proposed by the engineer, pending client approval."""

    model_config = ConfigDict(extra="forbid")

    text: str
    approved: bool = False


class Track(BaseModel):
    """One skill track (engineering / presentation_design / cv_resume)."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    experience_years: int | None = Field(default=None, ge=0)
    keywords_en: list[str] = Field(default_factory=list)
    keywords_ar_proposed: list[ArabicKeywordProposal] = Field(default_factory=list)

    @property
    def approved_keywords_ar(self) -> list[str]:
        """Arabic keywords the client has approved (``approved: true``) only."""
        return [item.text for item in self.keywords_ar_proposed if item.approved]

    @property
    def match_keywords(self) -> list[str]:
        """Keywords usable for matching: all English plus approved Arabic only."""
        return [*self.keywords_en, *self.approved_keywords_ar]


class UnconfirmedSkill(BaseModel):
    """A skill named in the signed proposal but not yet confirmed by the client."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    confirmed: bool = False


class ProposedTrack(BaseModel):
    """A candidate track that exceeds the written profile, awaiting client approval.

    Never used by matching until the client approves it (``approved: true``).
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    approved: bool = False
    rationale: str | None = None


class OutOfScopeDomain(BaseModel):
    """A domain to steer the AI away from and to penalise softly.

    Intentionally NOT a hard keyword exclusion. Only approved entries are applied.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    approved: bool = False


class ScoreBands(BaseModel):
    """Score band boundaries, which must satisfy ``strong > good > moderate``."""

    model_config = ConfigDict(extra="forbid")

    strong: int
    good: int
    moderate: int = 65
    weak: int = 65

    @model_validator(mode="after")
    def _check_band_order(self) -> ScoreBands:
        threshold = min(self.moderate, self.weak)
        if not (0 <= threshold < self.good < self.strong <= 100):
            msg = (
                "score_bands must satisfy 0 <= moderate/weak < good < strong <= 100 "
                f"(got moderate={self.moderate}, weak={self.weak}, "
                f"good={self.good}, strong={self.strong})"
            )
            raise ValueError(msg)
        return self


class ProfileSettings(BaseModel):
    """Matching thresholds and preferences, editable without code changes."""

    model_config = ConfigDict(extra="forbid")

    min_match_score: int = Field(ge=0, le=100)
    score_bands: ScoreBands
    notify_language: Literal["ar", "en"] = "ar"
    excluded_keywords: list[str] = Field(default_factory=list)
    min_budget: float | None = Field(default=None, ge=0)
    preferred_duration: str | None = None


class ClientProfile(BaseModel):
    """The whole editable client profile."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    titles: list[str]
    languages: list[str]
    tracks: list[Track]
    unconfirmed_skills: list[UnconfirmedSkill] = Field(default_factory=list)
    proposed_tracks: list[ProposedTrack] = Field(default_factory=list)
    out_of_scope_domains: list[OutOfScopeDomain] = Field(default_factory=list)
    settings: ProfileSettings

    @model_validator(mode="after")
    def _check_uniqueness(self) -> ClientProfile:
        if not self.tracks:
            raise ValueError("profile must define at least one track")
        track_ids = [track.id for track in self.tracks]
        if len(set(track_ids)) != len(track_ids):
            raise ValueError(f"track ids must be unique, got {track_ids}")
        for name, ids in (
            ("unconfirmed skill", [skill.id for skill in self.unconfirmed_skills]),
            ("proposed track", [track.id for track in self.proposed_tracks]),
            ("out-of-scope domain", [domain.id for domain in self.out_of_scope_domains]),
        ):
            if len(set(ids)) != len(ids):
                raise ValueError(f"{name} ids must be unique, got {ids}")
        proposed_ids = {track.id for track in self.proposed_tracks}
        collision = proposed_ids.intersection(track_ids)
        if collision:
            raise ValueError(f"proposed track ids must not clash with active tracks: {collision}")
        return self

    def track(self, track_id: str) -> Track:
        """Return a track by id."""
        for item in self.tracks:
            if item.id == track_id:
                return item
        raise KeyError(track_id)

    @property
    def confirmed_skills(self) -> list[str]:
        """Labels of previously unconfirmed skills the client has now confirmed."""
        return [skill.label for skill in self.unconfirmed_skills if skill.confirmed]

    @property
    def pending_skills(self) -> list[str]:
        """Labels of skills still awaiting confirmation (excluded from matching)."""
        return [skill.label for skill in self.unconfirmed_skills if not skill.confirmed]

    @property
    def approved_proposed_tracks(self) -> list[str]:
        """Ids of extra tracks the client has approved (empty until approved)."""
        return [track.id for track in self.proposed_tracks if track.approved]

    @property
    def active_out_of_scope_domains(self) -> list[str]:
        """Labels of out-of-scope domains approved for soft penalties."""
        return [domain.label for domain in self.out_of_scope_domains if domain.approved]

    def match_keywords(self) -> dict[str, list[str]]:
        """Per-track keywords available for matching (English plus approved Arabic).

        Note: ``proposed_tracks`` keywords are intentionally absent - proposed tracks are
        never used by matching until the client approves them.
        """
        return {item.id: item.match_keywords for item in self.tracks}

    def canonical_json(self) -> str:
        """Deterministic JSON serialisation used for hashing and snapshots."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    def content_hash(self) -> str:
        """Stable sha256 of the profile content; any change alters the hash."""
        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        """Serialisable snapshot for the future ``user_profile`` table."""
        return {
            "version": self.version,
            "content_hash": self.content_hash(),
            "payload": self.model_dump(mode="json"),
        }


def load_profile(path: str | Path) -> ClientProfile:
    """Load and validate a client profile from a YAML file."""
    profile_path = Path(path)
    if not profile_path.is_file():
        raise FileNotFoundError(f"profile file not found: {profile_path}")
    raw = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"profile YAML must contain a top-level mapping: {profile_path}")
    return ClientProfile.model_validate(raw)
