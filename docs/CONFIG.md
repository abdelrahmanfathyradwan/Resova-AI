# CONFIGURATION

Resova reads configuration from two places:

1. **Environment variables** (optionally via a local `.env` file) - machine/infra
   settings and secrets. Implemented in `src/resova/config/settings.py` with
   `pydantic-settings`. Template: [`.env.example`](../.env.example).
2. **`config/profile.yaml`** - the editable, human-facing client profile (tracks,
   keywords, thresholds). Never contains secrets. See
   [profile documentation](#client-profile-configprofileyaml) below.

Secrets travel **only** through environment variables. They are typed
`pydantic.SecretStr`, so they are masked in `repr()`, `str()` and JSON dumps and never
appear in logs. `Settings.safe_dump()` returns a log-safe view with each secret
replaced by `"***"` (or `None` when unset).

## Change class

| Class | Meaning |
| ----- | ------- |
| **[restart]** | Read once at startup. Changing it requires an application restart. |
| **[runtime]** | May be changed while the app runs (reserved for the `system_settings` table in a later phase). |

## Environment variables

| Variable | Default | Class | Secret | Notes |
| -------- | ------- | ----- | ------ | ----- |
| `APP_ENV` | `development` | restart | no | `development` \| `production` \| `test` |
| `LOG_LEVEL` | `INFO` | restart | no | `DEBUG` \| `INFO` \| `WARNING` \| `ERROR` \| `CRITICAL` |
| `DATABASE_URL` | `sqlite:///./resova.db` | restart | no | Portable SQLAlchemy URL; PostgreSQL later is a config change only |
| `PROFILE_PATH` | `config/profile.yaml` | restart | no | Path to the client profile YAML |
| `ENABLE_EMAIL_INGESTION` | `true` | restart | no | Master switch for reading the client's own alert mailbox |
| `ENABLE_MOSTAQL` | `true` | restart | no | Parse Mostaql alert emails (priority 1) |
| `ENABLE_KHAMSAT` | `true` | restart | no | Parse Khamsat alert emails (priority 2) |
| `EMAIL_POLL_MODE` | `polling` | restart | no | Only `polling` today - stdlib `imaplib` has no IDLE (see [OPEN_QUESTIONS](OPEN_QUESTIONS.md)) |
| `POLL_INTERVAL_SECONDS` | `60` | restart | no | 30-3600; 30-60 recommended |
| `IMAP_HOST` | _(empty)_ | restart | no | Required when `ENABLE_EMAIL_INGESTION=true` |
| `IMAP_USER` | _(empty)_ | restart | no | Required when `ENABLE_EMAIL_INGESTION=true` |
| `IMAP_PASSWORD` | _(unset)_ | restart | **yes** | Required when `ENABLE_EMAIL_INGESTION=true` |
| `ENABLE_AI_ANALYSIS` | `true` | restart | no | OpenAI-backed opportunity scoring |
| `OPENAI_API_KEY` | _(unset)_ | restart | **yes** | Required when `ENABLE_AI_ANALYSIS=true` |
| `OPENAI_MODEL` | `gpt-4o-mini` | restart | no | Model id (confirmed in Phase 4) |
| `DAILY_AI_ANALYSIS_LIMIT` | `100` | runtime | no | Cost guard rail (>= 1) |
| `ENABLE_TELEGRAM` | `true` | restart | no | Telegram notifications |
| `TELEGRAM_BOT_TOKEN` | _(unset)_ | restart | **yes** | Required when `ENABLE_TELEGRAM=true` |
| `TELEGRAM_ALLOWED_USER_ID` | _(unset)_ | restart | no | Required and `> 0` when `ENABLE_TELEGRAM=true` |

### Conditional secrets

A secret is validated **only when the feature that needs it is enabled**. If a feature
is enabled and its secret is missing, settings validation fails at startup with a clear
message listing the missing variable names (never their values), for example:

```
Missing required configuration for enabled features: IMAP_PASSWORD, OPENAI_API_KEY.
Provide them in .env, or disable the related feature flag.
```

Consequently the test suite runs with **zero real credentials**: tests disable the
features (or pass dummy placeholder values) and never touch a real secret.

## Client profile (`config/profile.yaml`)

The profile is the single source of truth for *what* Resova looks for and *how* it
scores. It is versioned with an explicit `version` plus a deterministic sha256
**content hash** (any change - a keyword, a threshold, an approval flag - changes the
hash). `ClientProfile.snapshot()` returns `{version, content_hash, payload}` for the
future `user_profile` table.

Two safety rules are encoded in the model:

- **Arabic keywords** live under `keywords_ar_proposed` with `approved: false`. They are
  only *proposed* by the engineer and are **not** used for matching until the client
  sets `approved: true`.
- **`unconfirmed_skills`** are named in the signed proposal but not in the client
  profile. They are excluded from matching and drafting until the client sets
  `confirmed: true`.

Profile fields worth calling out:

| Field | Value | Notes |
| ----- | ----- | ----- |
| `settings.min_match_score` | `70` | Notification threshold |
| `settings.score_bands` | `strong=85, good=70, weak=50` | Must satisfy `0 <= weak < good < strong <= 100` |
| `settings.notify_language` | `ar` | To confirm with client (OQ-7) |
| `settings.excluded_keywords` | `[]` | To confirm with client (OQ-8) |
| `settings.min_budget` | `null` | Budget is never an exclusion criterion |
| `settings.preferred_duration` | `null` | Informational only |

Note: match thresholds live in `config/profile.yaml`, not in environment variables, so
there is a single source of truth.
