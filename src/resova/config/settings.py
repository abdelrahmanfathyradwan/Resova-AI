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

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Fields that hold secrets and must be redacted from any log-safe output.
SECRET_FIELDS: tuple[str, ...] = (
    "imap_password",
    "openai_api_key",
    "telegram_bot_token",
)


def normalize_database_url(url: str) -> str:
    """Normalize database URL for SQLAlchemy compatibility.

    Railway, Heroku, and other PaaS providers frequently export ``postgres://...``
    which SQLAlchemy 1.4+ rejects in favor of ``postgresql://...``.
    """
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


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
    public_base_url: str = "https://resova-production.up.railway.app"

    # --- Database ---
    database_url: str = "sqlite:///./resova.db"

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_db_url(cls, v: object) -> str:
        if isinstance(v, str):
            return normalize_database_url(v)
        return str(v)

    # --- Client profile ---
    profile_path: Path = Path("config/profile.yaml")

    # --- Email ingestion (the client's own platform alert mailbox) ---
    enable_email_ingestion: bool = True
    # --- Listing poller (public listings) ---
    enable_listing_poller: bool = False
    listing_poller_ack: bool = False
    mostaql_listing_urls: Any = Field(default_factory=list)
    khamsat_listing_urls: Any = Field(default_factory=list)
    listing_poll_interval_seconds: int = Field(default=60, ge=60, le=3600)
    listing_poll_jitter_seconds: int = Field(default=10, ge=0)
    max_poll_pages: int = Field(default=1, ge=1, le=5)
    user_agent_contact: str = ""
    enable_mostaqel: bool = True
    enable_khamsat: bool = True
    max_notification_age_hours: int = Field(default=48, ge=1)
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
    telegram_owner_id: int | None = None

    @property
    def owner_chat_id(self) -> int | None:
        """The trusted system owner ID."""
        return (
            self.telegram_owner_id
            if self.telegram_owner_id is not None
            else self.telegram_allowed_user_id
        )

    @field_validator("mostaql_listing_urls", "khamsat_listing_urls", mode="before")
    @classmethod
    def _split_urls(cls, v: object) -> list[str]:
        """Accept JSON array or comma/space separated string for listing URLs.
        Returns a list of cleaned URLs.
        """
        import json

        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return [str(item).strip() for item in v]
        if isinstance(v, str):
            v = v.strip()
            if not v:
                return []
            # JSON array detection
            if v.startswith("["):
                try:
                    arr = json.loads(v)
                    if isinstance(arr, list):
                        return [str(item).strip() for item in arr]
                except Exception:
                    pass
            # Split on commas or whitespace
            parts = [
                p.strip() for p in v.replace("\n", ",").replace(";", ",").split(",") if p.strip()
            ]
            return parts
        return []

    @model_validator(mode="after")
    def _require_secrets_for_enabled_features(self) -> Settings:
        """Validate that required secrets are present for enabled features.
        Raises ValueError which Pydantic turns into ValidationError.
        """
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

        if self.enable_listing_poller:
            if not self.listing_poller_ack:
                missing.append("LISTING_POLLER_ACK (must be true to enable poller)")
            if not self.mostaql_listing_urls and not self.khamsat_listing_urls:
                msg = (
                    "MOTION: Provide at least one listing URL "
                    "(MOSTAQL_LISTING_URLS or KHAMSAT_LISTING_URLS)"
                )
                missing.append(msg)
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

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_db_url(cls, v: object) -> str:
        if isinstance(v, str):
            return normalize_database_url(v)
        return str(v)


def get_database_url() -> str:
    """Resolve only the database URL, without validating feature secrets."""
    return normalize_database_url(_DatabaseOnly().database_url)
