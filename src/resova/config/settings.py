"""Runtime configuration for Resova (Phase 1 / Step 2.2).

All configuration is read from environment variables, optionally via a local ``.env``
file. Secrets are typed :class:`pydantic.SecretStr`, so they never appear in ``repr()``,
``str()`` or JSON dumps. A secret is required only when the feature that needs it is
enabled, which keeps the test suite runnable with zero real credentials.

Change classes: values read once at startup are marked in ``.env.example`` as
``[restart]``; runtime-editable values are marked ``[runtime]``. See ``docs/CONFIG.md``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Fields that hold secrets and must be redacted from any log-safe output.
SECRET_FIELDS: tuple[str, ...] = (
    "imap_password",
    "openai_api_key",
    "telegram_bot_token",
)


def _is_blank(secret: SecretStr | None) -> bool:
    """Return ``True`` when a secret is missing or whitespace-only."""
    return secret is None or not secret.get_secret_value().strip()


class Settings(BaseSettings):
    """Environment-backed application settings.

    Instantiating this class validates the whole configuration. When a feature flag is
    enabled but its secret is missing, three validation fails with a clear message
    listing the missing variable names (never values).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---
    app_env: Literal["development", "production", "test"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # --- Database ---
    database_url: str = "sqlite:///./resova.db"

    # --- Client profile ---
    profile_path: Path = Path("config/profile.yaml")

    # --- Email ingestion (the client's own platform alert mailbox) ---
    enable_email_ingestion: bool = True
    enable_mostaqel: bool = True
    enable_khamsat: bool = True
    # ``off`` disables email polling entirely (dev/tests). Manual paste is a separate,
    # always-available channel handled by the Telegram bot (Phase 5), not a poll mode.
    email_poll_mode: Literal["polling", "off"] = "polling"
    poll_interval_seconds: int = Field(default=60, ge=30, le=3600)
    imap_host: str = ""
    imap_user: str = ""
    imap_password: SecretStr | None = None

    # --- OpenAI analysis ---
    enable_ai_analysis: bool = True
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    daily_ai_analysis_limit: int = Field(default=100, ge=1)

    # --- Telegram notifications ---
    enable_telegram: bool = True
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_user_id: int | None = None

    @model_validator(mode="after")
    def _require_secrets_for_enabled_features(self) -> Settings:
        missing: list[str] = []

        if self.enable_email_ingestion and self.email_poll_mode == "polling":
            if not self.imap_host.strip():
                missing.append("IMAP_HOST")
            if not self.imap_user.strip():
                missing.append("IMAP_USER")
            if _is_blank(self.imap_password):
                missing.append("IMAP_PASSWORD")

        if self.enable_ai_analysis and _is_blank(self.openai_api_key):
            missing.append("OPENAI_API_KEY")

        if self.enable_telegram:
            if _is_blank(self.telegram_bot_token):
                missing.append("TELEGRAM_BOT_TOKEN")
            if self.telegram_allowed_user_id is None or self.telegram_allowed_user_id <= 0:
                missing.append("TELEGRAM_ALLOWED_USER_ID")

        if missing:
            raise ValueError(
                "Missing required configuration for enabled features: "
                + ", ".join(missing)
                + ". Provide them in .env, or disable the related feature flag."
            )
        return self

    def safe_dump(self) -> dict[str, Any]:
        """Return settings as a plain dict with secrets redacted.

        Safe to log: each secret field is replaced by ``"***"`` when set and ``None``
        when unset.
        """
        data = self.model_dump()
        for field in SECRET_FIELDS:
            secret = getattr(self, field, None)
            data[field] = None if _is_blank(secret) else "***"
        return data


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached :class:`Settings` instance."""
    return Settings()


class _DatabaseOnly(BaseSettings):
    """Reads only ``DATABASE_URL`` from the environment/``.env``.

    Used by Alembic, which needs the database URL but must not require feature secrets
    (a migration on a fresh clone has to work before any secret is configured).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = "sqlite:///./resova.db"


def get_database_url() -> str:
    """Resolve only the database URL, without validating feature secrets."""
    return _DatabaseOnly().database_url
