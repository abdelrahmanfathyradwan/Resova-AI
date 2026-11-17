# Resova — AI Freelancer Agent

A personal assistant for **one** freelance engineer. It ingests new freelance opportunities
(Mostaql = priority 1, Khamsat = priority 2), filters them cheaply, scores each one 0–100
against the client profile with OpenAI, sends everything scoring **≥ `MIN_MATCH_SCORE`**
to Telegram, prevents duplicates and can later generate an editable proposal draft.

## Hard rules (never violated)

- **Compliance:** no scraping of Mostaql/Khamsat or any site; no bypassing authentication,
  CAPTCHA, bot protection or rate limits; no session cookies; no third-party scraping
  services; no automatic fetching of platform project URLs; platform passwords are never
  requested or stored.
- **No auto-apply:** Resova never submits proposals and never contacts clients. Drafts only,
  always reviewed manually.
- **No fabrication:** missing data stays `null` and is explicitly reported as *not provided*.
  Competition level is only shown when the source itself provides it.
- **Security:** secrets live only in environment variables (`SecretStr`), `.env` and `*.db`
  are git-ignored, and secrets never appear in logs, errors or Telegram messages.
- **Scope:** no web dashboard, no SaaS/multi-user, no platforms other than Mostaql/Khamsat,
  no payments. Future ideas go to `docs/FUTURE.md`, not code.

## Status — Phase 1, Step 2.1 (project skeleton)

| Implemented now | Not implemented yet (clearly marked stubs) |
| --- | --- |
| Repository, `.gitignore`, `.env.example`, `pyproject.toml`, `requirements.lock` | Configuration loading (Step 2.2) |
| `src/resova` package layout with phase-marked stubs | Client profile YAML loader (Step 2.3) |
| `scripts/tasks.py` task runner, smoke tests | Database models + Alembic migrations (Step 2.4) |
| `docs/` tree and full documentation (Step 2.9) | Repositories (Step 2.5), utilities (Step 2.6) |
| | Source interface (Step 2.7), `GET /health` (Step 2.8) |
| | Sources, scoring, Telegram bot, proposal drafts (Phase 2–6) |

`python scripts/tasks.py run` and `python scripts/tasks.py migrate` therefore exit with code `2`
and print an explicit `NOT IMPLEMENTED` message. They become functional in Steps 2.8 and 2.4.

## Requirements

- **Python 3.12** (project targets `>=3.12`).
  On Windows, `3.12.10` is the last 3.12 release published with a Windows installer; later
  3.12.x releases are source-only security releases. Verified against python.org.
- git.

## Install

Prefer the task runner (it locates a Python 3.12 interpreter for you):

```powershell
python scripts/tasks.py install
```

Equivalent raw commands (Windows / PowerShell):

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
```

Equivalent raw commands (Linux / macOS):

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: -r requirements.lock
.venv/bin/python -m pip install --no-deps -e .
```

## Configure

```powershell
Copy-Item .env.example .env
```

`.env` is git-ignored. Fill in real values only in `.env`; `.env.example` keeps placeholders.
Every variable is documented, with its restart/runtime behaviour, in `docs/CONFIG.md` (Step 2.9).

## Tasks

```powershell
python scripts/tasks.py install   # create .venv + install pinned dependencies
python scripts/tasks.py test      # pytest with coverage
python scripts/tasks.py lint      # ruff check + ruff format --check
python scripts/tasks.py run       # start the local API server (Step 2.8)
python scripts/tasks.py migrate   # apply alembic migrations (Step 2.4)
python scripts/tasks.py check     # sanity-check the checkout before committing
```

Raw equivalents:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=resova --cov-report=term-missing
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m alembic upgrade head                                            # Step 2.4
.\.venv\Scripts\python.exe -m uvicorn resova.api:app --host 127.0.0.1 --port 8000              # Step 2.8
```

## Dependency locking

`pyproject.toml` declares compatible ranges; `requirements.lock` holds the exact, verified
versions (and their transitive dependencies) and is what the install instructions use.

Regenerate the lock after changing a dependency version, from a virtualenv where the project
itself is installed editable:

```powershell
python scripts/tasks.py install
.\.venv\Scripts\python.exe -m pip freeze |
  Where-Object { $_ -notmatch '^-e ' } |
  Set-Content -Path requirements.lock -Encoding ASCII
```

The `-e ` (local editable project) line is filtered out on purpose: absolute local paths must
never be committed into a lock file.

## Layout

```
pyproject.toml         packaging, dependency ranges, ruff + pytest config
requirements.lock      exact pinned versions (generated from the clean virtualenv)
.env.example           environment template (placeholders only)
scripts/tasks.py       cross-platform task runner (argparse, standard library only)
src/resova/
  config/              configuration + client profile          -> Step 2.2 / 2.3
  db/                  SQLAlchemy models + repositories        -> Step 2.4 / 2.5
  sources/             BaseSource + Mostaql/Khamsat            -> Step 2.7 / Phase 2
  utils/               errors, retry, redacting logs           -> Step 2.6
  api/                 FastAPI application (GET /health)       -> Step 2.8
  services/ ai/ telegram/ scheduler/                           -> Phase 2-6
tests/                 pytest suite
docs/                  architecture, decisions, sources, config, open questions -> Step 2.9
```

## Git safety

The repository **must** be rooted at the project directory itself (this checkout), never inside
another repository such as a home directory. Before every commit:

```powershell
git rev-parse --show-toplevel   # must print this project's path
git status --short              # review the exact file list
```

`python scripts/tasks.py check` asserts the repository root automatically.
`.env`, `*.db`, `*.sqlite*`, logs and caches are git-ignored.
