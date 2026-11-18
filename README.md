# Resova — AI Freelancer Agent

A personal assistant for **one** freelance engineer. It ingests new freelance opportunities
(Mostaql = priority 1, Khamsat = priority 2), filters them cheaply, scores each one 0–100
against the client profile with OpenAI, sends everything scoring **≥ `min_match_score`** to
Telegram, prevents duplicates and can later generate an editable proposal draft.

Resova runs **locally**, for a single user. It never contacts a platform on its own.

## Hard rules (never violated)

> The normative rules live in [docs/PROJECT_RULES.md](docs/PROJECT_RULES.md). The summary
> below is a convenience copy; if the two ever differ, PROJECT_RULES.md wins.

- **Compliance:** no scraping of Mostaql/Khamsat or any site; no bypassing authentication,
  CAPTCHA, bot protection or rate limits; no session cookies; no third-party scraping
  services; no automatic fetching of platform project URLs; platform passwords are never
  requested or stored. The only permitted discovery channels are the client's **own** alert
  emails and **manual paste**.
- **No auto-apply:** Resova never submits a proposal and never contacts a client. Drafts only,
  always reviewed manually.
- **No fabrication:** missing data stays `null` and is reported as *not provided*. Competition
  level is shown only when the source itself provides it.
- **Secrets:** live only in environment variables (`SecretStr`); `.env` and `*.db` are
  git-ignored; secrets never appear in logs, errors or messages.
- **Nothing client-pending is used** (Arabic keywords, unconfirmed skills, proposed tracks,
  out-of-scope domains) until the client approves it with `approved: true` /
  `confirmed: true`.
- **Budget is never an exclusion criterion.**
- **Scope:** no web dashboard, no SaaS/multi-user, no platforms other than Mostaql/Khamsat,
  no payments. Future ideas go to [docs/FUTURE.md](docs/FUTURE.md), not into code.

## Status — Phase 1 complete

| Implemented | Not implemented yet |
| --- | --- |
| Settings + conditional secrets ([`settings.py`](src/resova/config/settings.py)) | Concrete Mostaql/Khamsat alert-email parsers — **"Not implemented - pending sample emails"** ([docs/SOURCES.md](docs/SOURCES.md)) |
| Versioned client profile ([`profile.py`](src/resova/config/profile.py)) | IMAP ingestion — Phase 3 |
| 9 ORM models + Alembic migrations ([`models.py`](src/resova/db/models.py)) | OpenAI scoring — Phase 4 |
| Idempotent repository with 3-key dedup ([`repositories.py`](src/resova/db/repositories.py)) | Telegram bot + manual paste — Phase 5 |
| Normalizer, dedup keys, errors, retry, redacting logs ([`utils/`](src/resova/utils)) | Proposal drafts — Phase 6 |
| Source contract + unified project model ([`sources/base.py`](src/resova/sources/base.py)) | Scheduler jobs — Phase 3+ |
| FastAPI `GET /health` on 127.0.0.1 ([`api/app.py`](src/resova/api/app.py)) | |

Nothing from a later phase is implemented. Packages that are still stubs (`services`, `ai`,
`telegram`, `scheduler`) say so in their docstring and are asserted by
[`tests/test_skeleton.py`](tests/test_skeleton.py).

## Requirements

- **Python 3.12** (project targets `>=3.12`).
  On Windows, `3.12.10` is the last 3.12 release published with a Windows installer; later
  3.12.x releases are source-only security releases. Verified against python.org.
- git.

## Quick start (fresh clone)

```powershell
git clone <repository-url> resova
cd resova

python scripts/tasks.py install     # create .venv + install the pinned dependencies
Copy-Item .env.example .env         # then edit .env (placeholders only in the template)
python scripts/tasks.py migrate     # apply Alembic migrations (creates resova.db)

python scripts/tasks.py run         # http://127.0.0.1:8000/health
```

`GET /health` answers `{"status":"ok","version":"0.1.0","database":"ok"}`. It needs **no
credential at all** — only `DATABASE_URL` — so it works on a fresh clone before anything is
configured. Linux/macOS use `python3 scripts/tasks.py …` and `cp .env.example .env`.

To verify the checkout without running it:

```powershell
python scripts/tasks.py test        # pytest with coverage
python scripts/tasks.py lint        # ruff check + ruff format --check
python scripts/tasks.py check       # repo root, ignores, dependencies, git status
```

## Configure

`.env` is git-ignored and must never be committed; `.env.example` holds placeholders only.
Every variable, its default and whether it needs a restart is documented in
[docs/CONFIG.md](docs/CONFIG.md). Business rules (tracks, keywords, score thresholds) live in
[`config/profile.yaml`](config/profile.yaml), not in environment variables.

A secret is validated **only when the feature that needs it is enabled**, so tests and the
health endpoint run with zero real credentials. If a feature is enabled and its secret is
missing, startup fails with the missing variable *names* (never values):

```
Missing required configuration for enabled features: IMAP_PASSWORD, OPENAI_API_KEY.
Provide them in .env, or disable the related feature flag.
```

## Tasks

```powershell
python scripts/tasks.py install   # create .venv + install pinned dependencies
python scripts/tasks.py test      # pytest with coverage
python scripts/tasks.py lint      # ruff check + ruff format --check
python scripts/tasks.py run       # start the local API server (loopback only)
python scripts/tasks.py migrate   # apply alembic migrations
python scripts/tasks.py check     # sanity-check the checkout before committing
```

Raw equivalents:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=resova --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m uvicorn resova.api:app --host 127.0.0.1 --port 8000
```

`run` refuses any host other than loopback: Resova is a local, single-user tool.

## Install (raw commands)

Windows / PowerShell:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

Linux / macOS:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
```

## Dependency locking

`pyproject.toml` declares compatible ranges; `requirements.lock` holds the exact, verified
versions (and their transitive dependencies) and is what the install instructions use. The
lock answers versions for a **development** install, so it includes `pytest`, `pytest-cov`,
`ruff` and `httpx` (the last only so `fastapi.testclient` can exercise the API in tests).

Regenerate the lock after changing a dependency version, from a virtualenv where the project
itself is installed editable:

```powershell
python scripts/tasks.py install
.\.venv\Scripts\python.exe -m pip freeze |
  Where-Object { $_ -notmatch '^-e ' -and $_ -notmatch '^#' } |
  Set-Content -Path requirements.lock -Encoding ASCII
```

Editable-install lines (`-e `) and pip's `# Editable …` comment are filtered out on purpose:
absolute local paths must never be committed into a lock file.

## Layout

```
pyproject.toml         packaging, dependency ranges, ruff + pytest config
requirements.lock      exact pinned versions (generated from the clean virtualenv)
alembic.ini            Alembic config; the URL comes from DATABASE_URL
migrations/            Alembic environment + versioned migrations
.env.example           environment template (placeholders only)
config/profile.yaml    client profile: tracks, keywords, thresholds (no secrets)
scripts/tasks.py       cross-platform task runner (argparse, standard library only)
src/resova/
  config/              settings + client profile                    -> Step 2.2 / 2.3
  db/                  models, session wiring, repository           -> Step 2.4 / 2.5
  sources/             BaseSource contract + unified model          -> Step 2.7
  utils/               normalizer, dedup keys, errors, retry, logs  -> Step 2.5 / 2.6
  api/                 FastAPI application (GET /health)            -> Step 2.8
  services/ ai/ telegram/ scheduler/                                -> Phase 2-6 (stubs)
tests/                 pytest suite
docs/                  project rules, architecture, decisions, sources, config,
                       future, open questions
```

## Documentation

| Document | Contents |
| -------- | -------- |
| [PROJECT_RULES](docs/PROJECT_RULES.md) | The non-negotiable rules (normative) |
| [ARCHITECTURE](docs/ARCHITECTURE.md) | Runtime shape, package map, data model, dedup |
| [DECISIONS](docs/DECISIONS.md) | Why each decision was taken, and its cost |
| [SOURCES](docs/SOURCES.md) | Permitted discovery channels and per-source status |
| [CONFIG](docs/CONFIG.md) | Every environment variable and every profile field |
| [FUTURE](docs/FUTURE.md) | Explicitly out-of-scope ideas |
| [OPEN_QUESTIONS](docs/OPEN_QUESTIONS.md) | Unresolved items, each with an owner |

## Git safety

The repository **must** be rooted at the project directory itself (this checkout), never inside
another repository such as a home directory. Before every commit:

```powershell
git rev-parse --show-toplevel   # must print this project's path
git status --short              # review the exact file list
```

`python scripts/tasks.py check` asserts the repository root automatically.
`.env`, `*.db`, logs and caches are git-ignored.
