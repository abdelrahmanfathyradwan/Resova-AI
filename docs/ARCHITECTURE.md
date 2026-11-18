# ARCHITECTURE

How Resova is put together, what exists today, and what deliberately does not.

Phase scope: **[PROJECT_RULES](PROJECT_RULES.md)** is normative; this document describes the
shape of the system. Decisions and their rationale are in [DECISIONS](DECISIONS.md).

## 1. What Resova is

A **local, single-user** assistant for one freelance engineer. It learns about new
opportunities (Mostaql first, Khamsat second), filters them cheaply, scores them 0-100
against the client profile with OpenAI, notifies the human on Telegram for everything at or
above a threshold, prevents duplicates, and can later prepare an **editable draft** proposal.

It never contacts a platform, never submits anything, and never scrapes. It is a program the
owner runs on their own machine, not a service.

## 2. Runtime shape

```
                    +--------------------------+
   .env  ---------> |  resova.config.settings  |  env + secrets (SecretStr)
                    +--------------------------+
                    |  config/profile.yaml     |  tracks, keywords, thresholds
                    +------------+-------------+
                                 |
   client's own alert mailbox    |        manual paste (Telegram, Phase 5)
              |                  |                     |
              v                  v                     v
        +-----------------------------------------------+
        |  sources/  BaseSource -> RawProject            |   Phase 2 parsers
        +-----------------------+-----------------------+
                                |  UnifiedProject
                                v
        +-----------------------------------------------+
        |  db/repositories  ProjectRepository.upsert    |   idempotent, dedup keys
        +-----------------------+-----------------------+
                                |
                                v
        +-----------------------------------------------+
        |  SQLAlchemy 2.x  ->  SQLite (MVP)             |   9 tables + alembic_version
        +-----------------------------------------------+
                                |
             (Phase 4 scoring / Phase 5 notify / Phase 6 drafts)
                                |
                                v
        +-----------------------------------------------+
        |  api/  FastAPI  GET /health  (127.0.0.1 only) |   Phase 1 endpoint
        +-----------------------------------------------+
```

Everything above the `sources/` row is implemented. The scoring, notification and drafting
layers are not - they belong to Phases 4, 5 and 6.

## 3. Package map

| Package | Contents | Status |
| ------- | -------- | ------ |
| `resova.config` | `Settings` (env + secrets), `ClientProfile` (YAML, versioned + content hash) | Implemented (Steps 2.2 / 2.3) |
| `resova.db` | `Base`, the 9 ORM models, engine/session wiring, `ProjectRepository` | Implemented (Steps 2.4 / 2.5) |
| `resova.utils` | dedup identity keys, URL/text normalizer, error hierarchy, retry, redacting logging | Implemented (Steps 2.5 / 2.6) |
| `resova.sources` | `BaseSource` contract + unified project model | Implemented (Step 2.7); concrete parsers pending samples |
| `resova.api` | FastAPI app with a single `GET /health` | Implemented (Step 2.8) |
| `resova.services` | ingestion/notify orchestration | Not implemented - Phase 4-6 |
| `resova.ai` | OpenAI scoring client | Not implemented - Phase 4 |
| `resova.telegram` | bot, notifications, manual paste | Not implemented - Phase 5 |
| `resova.scheduler` | APScheduler jobs | Not implemented - Phase 3+ |

## 4. Data model

Nine tables plus Alembic's own `alembic_version` (see `src/resova/db/models.py`):

| Table | Purpose |
| ----- | ------- |
| `projects` | One normalized opportunity; carries the three dedup keys |
| `source_messages` | Raw ingested alert emails, deduplicated by RFC 5322 `Message-ID` |
| `project_analyses` | AI scoring result, with an explicit `analyzed_at` |
| `notifications` | Telegram sends (deduplicates what was sent) |
| `user_actions` | What the human did with a project |
| `proposal_drafts` | Editable drafts - never sent automatically |
| `sync_logs` | One poll/sync run per source |
| `user_profile` | Immutable, versioned client-profile snapshots |
| `system_settings` | Runtime-editable key/value overrides |

Portability rules:

- Enums are stored as `VARCHAR` + `CHECK` (`native_enum=False`), never as native enum types.
- All timestamps are **naive UTC** (`resova.db.base.utcnow`).
- Only portable SQL is used; no backend-specific functions.
- SQLite runs with `PRAGMA foreign_keys=ON` and WAL, applied through a dialect-guarded hook,
  so moving to PostgreSQL is a `DATABASE_URL` change and nothing else.

## 5. Deduplication

A project can arrive twice through different channels (an alert email today, a manual paste
next week) and each channel carries a different subset of fields. `projects` therefore has
**three nullable, unique** keys, computed in the application layer
(`resova.utils.identity`) and never in SQL:

| Key | Computed from | Present when |
| --- | ------------- | ------------ |
| `url_key` | the normalized project URL | a URL is known |
| `external_key` | platform + the platform's own id | an external id is known |
| `content_key` | normalized title **and** normalized description | the description is at least ~40 chars |

Multiple `NULL`s never collide, so a missing key simply never matches.

`ProjectRepository.upsert_project` computes **every available key**, looks them all up, and:

- **exactly one row matches** -> update it, and **backfill** any key slots still `NULL`
  (a manual paste followed later by an email fills in `url_key` / `external_key`);
- **different rows match** -> never crash and never violate `UNIQUE`: log a `WARNING`, keep
  the **earliest** row, record the conflict in the result, and leave the other row untouched;
- **no row matches** -> insert;
- **`IntegrityError`** (a race between two writers) -> roll back to the savepoint and re-read
  inside the same transaction.

Keys are **write-once**: an established key is never overwritten, so an update can never
collide with another row's key.

Rationale for the conservative rule: a *false duplicate* silently hides a real opportunity,
while a *false new* only costs one extra notification. Title-only or budget-only matches are
therefore never treated as duplicates.

## 6. Configuration and secrets

Two sources only:

- **Environment variables** (`resova.config.settings.Settings`, via `.env`) - infra and
  secrets. Secrets are `SecretStr`.
- **`config/profile.yaml`** - the human-facing client profile; never contains secrets; carries
  a version and a deterministic content hash. Thresholds live here, not in env.

A secret is required **only when its feature flag is enabled**, so the test suite runs with
zero real credentials. Two paths intentionally read `DATABASE_URL` alone and skip all secret
validation: Alembic (`get_database_url`) and the API's health check. A fresh clone can migrate
and start the server before any credential exists.

## 7. Failure handling

- `resova.utils.errors` - one base `ResovaError` with a `retryable` flag. `SourceError`
  carries a `SourceFailureReason`; only `rate_limited` and `unavailable` are retryable.
- `resova.utils.retry` - bounded exponential backoff with jitter, with `sleep` and `rand`
  injectable so tests never wait.
- `resova.utils.logging_setup` - a `RedactionFilter` that removes both known secret values and
  secret-*shaped* strings (Telegram tokens, OpenAI keys, `key=value`, URL query params) from
  every record, and collapses the message to a pre-formatted string so `record.args` cannot
  leak.

## 8. The API

Exactly one endpoint: `GET /health`, reporting `{status, version, database}` (HTTP 200 when
the database answers `SELECT 1`, 503 when it does not). The application is bound to
**`127.0.0.1` only** - `scripts/tasks.py run` refuses any other host. The framework's
documentation routes (`/docs`, `/redoc`, `/openapi.json`) are disabled so no other surface
exists. No endpoint exposes client data or a secret.

## 9. What is deliberately not here

- No scraping, no headless browser, no session cookies, no third-party scraper - ever.
- No request is ever made to a Mostaql/Khamsat project URL.
- No auto-apply, no message sent to any client.
- No web dashboard, no multi-user, no payments.
- No parser for either platform yet: that needs real sample alert emails
  (see [SOURCES](SOURCES.md)).
