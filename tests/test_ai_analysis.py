"""Tests for AI project analysis (Phase 4).

Tests analyzer behavior with and without the OpenAI API, checking scoring,
profile skill matching, risk detection, error handling, and daily limit enforcement.
All tests run with zero real credentials and zero external network calls.
"""

from __future__ import annotations

import json
import sys
import types
from unittest.mock import MagicMock

import pytest
from pydantic import SecretStr

from resova.ai.analyzer import AIAnalyzer, AnalysisResult, _build_user_prompt, _score_to_band
from resova.config.profile import ClientProfile, load_profile
from resova.config.settings import Settings
from resova.db.models import ScoreBand
from resova.utils.errors import AIError


@pytest.fixture()
def profile(tmp_path) -> ClientProfile:
    return load_profile("config/profile.yaml")


@pytest.fixture()
def ai_settings() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=True,
        openai_api_key=SecretStr("sk-test-key-1234567890123456"),
        openai_model="gpt-4o-mini",
        daily_ai_analysis_limit=5,
        enable_telegram=False,
    )


@pytest.fixture()
def disabled_ai_settings() -> Settings:
    return Settings(
        _env_file=None,
        enable_email_ingestion=False,
        enable_ai_analysis=False,
        enable_telegram=False,
    )


def test_can_analyze_when_enabled(ai_settings: Settings) -> None:
    analyzer = AIAnalyzer(ai_settings)
    assert analyzer.can_analyze() is True


def test_cannot_analyze_when_disabled(disabled_ai_settings: Settings) -> None:
    analyzer = AIAnalyzer(disabled_ai_settings)
    assert analyzer.can_analyze() is False


def test_analyze_raises_when_disabled(disabled_ai_settings: Settings) -> None:
    analyzer = AIAnalyzer(disabled_ai_settings)
    with pytest.raises(AIError, match="disabled"):
        analyzer.analyze(title="Test Project")


def test_keyword_fallback_scoring(ai_settings: Settings) -> None:
    analyzer = AIAnalyzer(ai_settings)
    desc = (
        "Looking for an engineer to design a sprinkler system and "
        "fire pump with AutoCAD 2D drawings."
    )
    result = analyzer.analyze_without_api(
        title="Fire Fighting System Design for Commercial Building",
        description=desc,
        platform="mostaql",
    )
    assert isinstance(result, AnalysisResult)
    assert result.score > 0
    assert result.primary_track == "engineering"
    assert (
        "Fire Fighting System Design" in result.matching_skills
        or "Fire Fighting" in result.matching_skills
        or "AutoCAD 2D" in result.matching_skills
    )
    assert result.model == "keyword-scorer"


def test_keyword_fallback_with_unrelated_project(ai_settings: Settings) -> None:
    analyzer = AIAnalyzer(ai_settings)
    result = analyzer.analyze_without_api(
        title="Python Django Web Backend Development",
        description="Build a REST API using Django and PostgreSQL.",
        platform="mostaql",
    )
    assert result.score == 0
    assert result.band == ScoreBand.below
    assert result.recommendation == "skip"


def test_analyze_with_mocked_openai(ai_settings: Settings) -> None:
    analyzer = AIAnalyzer(ai_settings)

    mock_response_data = {
        "score": 88,
        "band": "strong",
        "primary_track": "engineering",
        "matching_skills": ["Fire Fighting System Design", "AutoCAD 2D"],
        "missing_skills": [],
        "summary": "Excellent match for fire fighting system design.",
        "rationale": "Matches primary track engineering with multiple confirmed skills.",
        "risks": ["Short delivery timeline"],
        "language": "ar",
        "recommendation": "notify",
    }

    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(mock_response_data)
    mock_completion = MagicMock()
    mock_completion.choices = [mock_choice]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = mock_completion

    # Create mock openai module in sys.modules
    mock_openai = types.ModuleType("openai")
    mock_openai.OpenAI = MagicMock(return_value=mock_client)  # type: ignore[attr-defined]

    orig_openai = sys.modules.get("openai")
    sys.modules["openai"] = mock_openai
    try:
        desc = "مطلوب مهندس ميكانيكا لتصميم شبكة إطفاء وحسابات هيدروليكية ومخططات أوتوكاد للمبنى."
        result = analyzer.analyze(
            title="تصميم شبكة مكافحة حريق",
            description=desc,
            platform="mostaql",
        )
    finally:
        if orig_openai is not None:
            sys.modules["openai"] = orig_openai
        else:
            sys.modules.pop("openai", None)

    assert result.score == 88
    assert result.band == ScoreBand.strong
    assert result.primary_track == "engineering"
    assert result.recommendation == "notify"
    assert "Short delivery timeline" in result.risks
    assert analyzer._daily_count == 1


def test_daily_limit_enforced(ai_settings: Settings) -> None:
    analyzer = AIAnalyzer(ai_settings)
    analyzer._daily_count = 5  # limit is 5

    assert analyzer.can_analyze() is False
    with pytest.raises(AIError, match="Daily AI analysis limit reached"):
        analyzer.analyze(title="Some Project")


def test_score_to_band_thresholds(profile: ClientProfile) -> None:
    assert _score_to_band(90, profile) == ScoreBand.strong
    assert _score_to_band(75, profile) == ScoreBand.good
    assert _score_to_band(66, profile) == ScoreBand.moderate
    assert _score_to_band(30, profile) == ScoreBand.below


def test_build_user_prompt(profile: ClientProfile) -> None:
    prompt = _build_user_prompt(
        title="Fire Alarm Design",
        description="Need design for a 5-story warehouse.",
        platform="mostaql",
        url="https://mostaql.com/projects/123",
        category="Engineering",
        profile=profile,
    )
    assert "Fire Alarm Design" in prompt
    assert "mostaql" in prompt
    assert "warehouse" in prompt
