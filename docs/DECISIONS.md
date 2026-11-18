# DECISIONS

The architecture decisions taken so far, why, and what they cost. Each entry is short and
normative: if a change contradicts an entry here, either the entry is amended first (with a
reason) or the change is rejected. Hard rules live in [PROJECT_RULES](PROJECT_RULES.md).

Format: **Context -> Decision -> Consequences**. "Status: accepted" unless stated otherwise.

---

## D-1 - Python 3.12, `src/` layout, setuptools

**Context.** A single-engineer project that must install and run from a README on both
Windows and Linux.

**Decision.** Target `>=3.12` (verified: 3.12.10 is the last 3.12 release with a Windows
installer). Use a `src/` layout so tests exercise the *installed* package rather than the
working directory. Packaging is plain setuptools.

**Consequences.** No dependency on a build backend beyond setuptools. Contributors must
install the package (`-e .`) before tests; `tests/conftest.py` and `pyproject.toml` assume it.

---

## D-2 - SQLAlchemy 2.x + Alembic, SQLite for the MVP, portable SQL only

**Context.** The MVP is a local tool for one person; a server-grade database is not needed
yet, but the schema must not be welded to SQLite.

**Decision.** SQLAlchemy 2.x ORM with `DeclarativeBase` and `Mapped`/`mapped_column`.
Schema changes go through Alembic migrations (never `create_all` in production paths). SQLite
is the MVP database, tuned only through a **dialect-guarded** hook (`foreign_keys=ON` + WAL).
Enums use `native_enum=False` (VARCHAR + CHECK). Timestamps are naive UTC.

**Consequences.** Moving to PostgreSQL is a `DATABASE_URL` change. Costs: no native enum
types, no backend-specific SQL (e.g. no `julianday()`), and timezone handling is the
application's responsibility.

---

## D-3 - Deduplication by three nullable unique keys

**Context.** The same opportunity arrives through more than one channel, each with a
different subset of fields. Alert emails are often partial, so no single field is always
present.

**Decision.** `projects` carries `url_key`, `external_key` and `content_key`, all
`String(64)`, `nullable=True`, `unique=True`. Values are sha256 hashes with a
type prefix, computed in the application layer.

**Consequences.** Multiple `NULL`s never collide on any backend, so a missing key is inert.
Three indexes instead of one. Hashing in Python means the database cannot compute keys for
raw-inserted rows - acceptable, since all writes go through the repository.

---

## D-4 - `content_key` = normalized title + normalized **description** (>= ~40 chars)

**Context.** Some opportunities are never given a URL or a platform id. Generic titles such
as "professional CV design" repeat constantly, so title-only equality would be a bad key.

**Decision.** `content_key` hashes the normalized title together with the normalized
description, and is `NULL` unless a description of at least 40 characters is present.
**Title-only and budget-only matches are never duplicates.**

**Consequences.** A genuinely duplicate project with a short or missing description may be
stored twice and notified twice. That is the accepted trade-off: a *false duplicate* hides a
real opportunity, while a *false new* only costs one extra notification. `40` is a judgement
call in one constant (`MIN_DESCRIPTION_LENGTH`) and easy to revisit.

---

## D-5 - Normalize text and URLs before hashing

**Context.** The same project produces different byte strings across channels: Arabic
tashkeel and tatweel, `أ/إ/آ` vs `ا`, `ى` vs `ي`, `ة` vs `ه`, percent-encoded Arabic slugs,
trailing slashes, uppercase hosts.

**Decision.** Before hashing: Unicode **NFKC**, strip tashkeel and tatweel, fold alef / ya /
ta-marbuta variants, collapse whitespace, lowercase Latin; for URLs also lowercase
scheme+host, drop the default port, percent-decode path and query, and drop the fragment.

**Consequences.** The normalizer never performs I/O (proved by a test that monkeypatches
`socket`). **No platform id format is assumed inside a URL** - id extraction waits for real
emails: `TODO(unverified)`.

---

## D-6 - Idempotent upsert: backfill, keep-the-earliest on conflict, savepoint on race

**Decision.** `ProjectRepository.upsert_project` computes all available keys and looks them
all up:

| Match | Behaviour |
| ----- | --------- |
| one row | update, and **backfill** keys that are still `NULL` |
| several rows | never crash, never violate `UNIQUE`: log `WARNING`, keep the **earliest** row, record the conflict, leave the others untouched |
| no row | insert |
| `IntegrityError` | roll back to a `SAVEPOINT` (`begin_nested`) and re-read inside the transaction |

Keys are **write-once**: an established key is never overwritten, so an update can never
collide with another row's key.

**Consequences.** Duplicate detection improves as more information arrives (manual paste
first, email later, or the reverse). Conflicts are surfaced, not silently resolved. The
conflict and race paths are covered by tests.

---

## D-7 - Only the client's own alert emails and manual paste

**Context.** Neither Mostaql nor Khamsat offers an official public API for this purpose
(verified), and Mostaql runs bot protection.

**Decision.** Two permitted channels only: the client's own notification mailbox (read with
consent, via IMAP) and manual paste. **No scraping, no bypassing protection, no session
cookies, no third-party scraping service, and no automatic fetch of a platform project URL.**

**Consequences.** Coverage depends on what the platform chooses to email; the manual-paste
channel is the fallback and a full substitute. See [SOURCES](SOURCES.md) and OQ-4.

---

## D-8 - No auto-apply, ever

**Decision.** Resova never submits a proposal, never messages a client and never accepts a
project. Automation stops at an editable **draft**.

**Consequences.** The human stays in the loop by construction. `proposal_drafts` exists to
store drafts, and no code path sends one.

---

## D-9 - Configuration: env for infra/secrets, `profile.yaml` for business rules

**Decision.** Secrets and machine settings come from environment variables
(`pydantic-settings`, `SecretStr`). Tracks, keywords and thresholds live in
`config/profile.yaml`, which is versioned and hashed. A secret is mandatory **only** when its
feature flag is enabled. Alembic and the health check read `DATABASE_URL` alone.

**Consequences.** A fresh clone can migrate and start the server with no credentials. There
is exactly one source of truth for thresholds. Changing a profile changes its content hash,
which makes the change detectable.

---

## D-10 - Errors, retry and redaction as shared utilities

**Decision.** One `ResovaError` base with a `retryable` flag; `SourceError` carries a
`SourceFailureReason` and only `rate_limited` / `unavailable` are retryable. Retry is bounded
exponential backoff with jitter, with `sleep` and `rand` injectable. Logging passes through a
`RedactionFilter` that removes known secrets and secret-shaped strings.

**Consequences.** Tests never sleep and never rely on randomness. A permanent error (bad
credentials, invalid data) is never retried into a loop. Any new secret must be added to
`SECRET_FIELDS` **and** covered by a redaction pattern.

---

## D-11 - API: one endpoint, loopback only, docs disabled

**Decision.** A FastAPI app exposing exactly `GET /health`, bound to `127.0.0.1`.
`/docs`, `/redoc` and `/openapi.json` are disabled, and `scripts/tasks.py run` refuses any
non-loopback host.

**Consequences.** The smallest possible attack surface and a literal one-endpoint API. If a
second endpoint is ever wanted it needs a new decision here. `httpx` is added as a
test-only dependency so `fastapi.testclient.TestClient` can exercise the app.

---

## D-12 - Task runner instead of a Makefile

**Context.** The project must be usable on Windows *and* Linux, and Make is not available by
default on Windows.

**Decision.** `scripts/tasks.py` - a standard-library `argparse` runner: `install`, `test`,
`lint`, `run`, `migrate`, `check`.

**Consequences.** No extra tooling, identical commands on every platform. `run` enforces the
loopback rule; `check` asserts the repository root and that `.env`/`*.db` are ignored.

---

## D-13 - `docs/PROJECT_RULES.md` is the normative rule set

**Context.** The conversation context that produced the rules is not durable; the repository
is.

**Decision.** The non-negotiable rules (compliance, no auto-apply, no fabrication, secrets,
git safety, scope, pending-client gates, budget-never-excludes) live in
[PROJECT_RULES](PROJECT_RULES.md), referenced from the README. The README carries only a
summary, and PROJECT_RULES wins on any divergence.

**Consequences.** A new contributor can find every rule without reading the chat history.

---

## D-14 - Nothing client-pending is used until it is approved

**Decision.** Arabic keywords (`approved: false`), unconfirmed skills (`confirmed: false`),
proposed tracks (`approved: false`) and out-of-scope domains (`approved: false`) are recorded
in `config/profile.yaml` but are **inert** for matching and drafting. Out-of-scope domains are
a **soft** score penalty plus AI prompt guidance - never a hard keyword exclusion.

**Consequences.** The profile can be reviewed without changing behaviour. Flipping a flag
requires the client's explicit approval (see OQ-5, OQ-6, OQ-9, OQ-12).

---

## D-15 - Owner names in documents are roles only

**Decision.** `docs/OPEN_QUESTIONS.md` names owners as `owner`, `dev` and `client` - never a
person's name, because the document may be delivered to the client.

**Consequences.** Documents are safe to share. Ownership is unambiguous without naming people.

---

## Decision log (chronological commits)

| Commit | Subject |
| ------ | ------- |
| `e028db4` | chore: bootstrap project tooling and dependency lock |
| `fddc50a` | feat(skeleton): add src layout with phase-marked stubs and smoke tests |
| `3a0132e` | chore: normalize text files to LF via .gitattributes |
| `0b734c4` | feat(config): add env settings with conditional secrets and versioned client profile |
| `2490ef0` | feat(config): record pending client items and add email_poll_mode=off |
| `3ac040b` | test(fixtures): add labeled projects dataset with loader/validation tests |
| `ebf843c` | docs: use role-only owner names and add PROJECT_RULES |
| `dac220f` | feat(db): add SQLAlchemy models, session wiring and Alembic migrations |
| `a7f6460` | feat(repo): add dedup identity keys, URL normalizer and project repository |
| `fdbc9cf` | feat(utils): add error hierarchy, bounded retry and logging redaction |
| `b5ba97f` | feat(sources): add source contract and unified project model |
| `3a5b9ad` | feat(api): add local FastAPI app with a single GET /health |
