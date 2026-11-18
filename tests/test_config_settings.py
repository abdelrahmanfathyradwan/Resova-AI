"""Tests for Phase 1 / Step 2.2 - environment-backed settings.

All tests run with zero real credentials: features are disabled or given dummy values.
"""

from __future__ import annotations

import logging

import pytest
from pydantic import ValidationError

from resova.config.settings import Settings, get_database_url, get_settings

SECRET = "sk-live-do-not-log-me-0123456789"


def _settings(**overrides: object) -> Settings:
    """Build settings with every secret-bearing feature disabled by default."""
    base: dict[str, object] = {
        "_env_file": None,
        "enable_email_ingestion": False,
        "enable_ai_analysis": False,
        "enable_telegram": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- feature / secret coupling -------------------------------------------------


def test_all_features_disabled_needs_no_secrets() -> None:
    settings = _settings()
    assert settings.imap_password is None
    assert settings.openai_api_key is None
    assert settings.telegram_bot_token is None


def test_defaults_are_sane_when_features_disabled() -> None:
    settings = _settings()
    assert settings.app_env == "development"
    assert settings.database_url == "sqlite:///./resova.db"
    assert settings.email_poll_mode == "polling"
    assert settings.poll_interval_seconds == 60
    assert settings.daily_ai_analysis_limit == 100


def test_email_ingestion_enabled_without_password_fails_clearly() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            enable_email_ingestion=True,
            enable_ai_analysis=False,
            enable_telegram=False,
            imap_host="imap.example.com",
            imap_user="alerts@example.com",
        )
    message = str(excinfo.value)
    assert "IMAP_PASSWORD" in message
    assert "disable the related feature flag" in message


def test_email_ingestion_without_host_or_user_lists_them() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            enable_email_ingestion=True,
            enable_ai_analysis=False,
            enable_telegram=False,
        )
    message = str(excinfo.value)
    assert "IMAP_HOST" in message
    assert "IMAP_USER" in message
    assert "IMAP_PASSWORD" in message


def test_email_poll_mode_off_needs_no_imap_config() -> None:
    settings = _settings(enable_email_ingestion=True, email_poll_mode="off")
    assert settings.email_poll_mode == "off"
    assert settings.imap_password is None


def test_invalid_email_poll_mode_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(email_poll_mode="manual")


def test_ai_analysis_enabled_without_key_fails_clearly() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            enable_email_ingestion=False,
            enable_ai_analysis=True,
            enable_telegram=False,
        )
    assert "OPENAI_API_KEY" in str(excinfo.value)


def test_telegram_enabled_without_token_fails_clearly() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Settings(  # type: ignore[call-arg]
            _env_file=None,
            enable_email_ingestion=False,
            enable_ai_analysis=False,
            enable_telegram=True,
        )
    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "TELEGRAM_ALLOWED_USER_ID" in message


def test_feature_enabled_with_secret_passes() -> None:
    settings = _settings(enable_ai_analysis=True, openai_api_key=SECRET)
    assert settings.openai_api_key is not None
    assert settings.openai_api_key.get_secret_value() == SECRET


def test_email_ingestion_with_all_values_passes() -> None:
    settings = _settings(
        enable_email_ingestion=True,
        imap_host="imap.example.com",
        imap_user="alerts@example.com",
        imap_password="dummy-imap-password",
    )
    assert settings.imap_password is not None


# --- secret redaction ----------------------------------------------------------


def test_secret_never_in_repr_or_str() -> None:
    settings = _settings(enable_ai_analysis=True, openai_api_key=SECRET)
    assert SECRET not in repr(settings)
    assert SECRET not in str(settings)
    assert SECRET not in settings.model_dump_json()


def test_secret_never_in_log_output(caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings(enable_ai_analysis=True, openai_api_key=SECRET)
    logger = logging.getLogger("resova.tests")
    with caplog.at_level(logging.DEBUG):
        logger.info("settings=%r", settings)
        logger.info("safe_dump=%s", settings.safe_dump())
        logger.info("full_dump=%s", settings.model_dump())
    assert SECRET not in caplog.text
    assert "***" in caplog.text


def test_safe_dump_redacts_secrets() -> None:
    settings = _settings(
        enable_email_ingestion=True,
        imap_host="imap.example.com",
        imap_user="alerts@example.com",
        imap_password="super-secret-imap-password",
    )
    dumped = settings.safe_dump()
    assert dumped["imap_password"] == "***"
    assert "super-secret-imap-password" not in str(dumped)


def test_safe_dump_marks_unset_secret_as_none() -> None:
    settings = _settings()
    assert settings.safe_dump()["openai_api_key"] is None


# --- numeric validation --------------------------------------------------------


def test_poll_interval_below_minimum_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(poll_interval_seconds=5)


def test_poll_interval_above_maximum_rejected() -> None:
    with pytest.raises(ValidationError):
        _settings(poll_interval_seconds=99999)


def test_daily_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        _settings(daily_ai_analysis_limit=0)


# --- factory -------------------------------------------------------------------


def test_get_settings_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_EMAIL_INGESTION", "false")
    monkeypatch.setenv("ENABLE_AI_ANALYSIS", "false")
    monkeypatch.setenv("ENABLE_TELEGRAM", "false")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    get_settings.cache_clear()
    try:
        settings = get_settings()
        assert settings.log_level == "DEBUG"
        assert settings.enable_telegram is False
    finally:
        get_settings.cache_clear()


# --- database URL helper (used by Alembic) -------------------------------------


def test_get_database_url_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./from-env.db")
    assert get_database_url() == "sqlite:///./from-env.db"


def test_get_database_url_needs_no_feature_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    # Email, AI and Telegram are all enabled by default, yet resolving the database URL
    # must not require their secrets (a fresh clone has to be able to migrate).
    monkeypatch.setenv("DATABASE_URL", "sqlite:///./db-only.db")
    assert get_database_url() == "sqlite:///./db-only.db"
