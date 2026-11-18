# PROJECT RULES

The non-negotiable rules for **Resova - AI Freelancer Agent**. These are binding for
every change, review and decision in this repository. If a request conflicts with a rule
below, the rule wins and the conflict must be raised instead of worked around.

This document is referenced from the [README](../README.md) and is intentionally short
and normative. Longer background lives in [DECISIONS](DECISIONS.md).

## 1. Compliance (never violated)

Resova collects opportunities **only** from sources the client is entitled to read:

- **No scraping** of Mostaql, Khamsat or any other site (no HTML scraping, no headless
  browsers, no page parsing of a platform we do not own).
- **No bypassing** authentication, CAPTCHA, bot protection, rate limits or any access
  control. If a platform defends itself, we stop - we do not defeat it.
- **No session cookies** and no cookie/session replay of any kind.
- **No third-party scraping services** (paid or free scrapers, proxy scrapers, etc.).
- **No automatic fetching of platform project URLs.** Resova never issues a network
  request to a Mostaql/Khamsat project URL. URLs are stored and shown to the human only;
  the human opens them.
- Platform passwords are **never** requested, stored or logged.

Permitted discovery channels:

1. The **client's own** platform notification emails (the client's own mailbox, read with
   the client's consent), and
2. **Manual paste** of project text by the client (a Telegram channel, Phase 5).

## 2. No auto-apply

Resova **never** submits a proposal, never sends a message to a client and never accepts
a project on its own. It may only produce **editable draft proposals** that the human
reviews, edits and sends manually. Automation stops at the draft.

## 3. No fabrication

- Missing data is stored as `null` and reported to the human as *not provided* - never
  invented, never guessed, never filled with a plausible-looking default.
- Competition level (number of offers / comments) is shown **only** when the source
  itself provides it.
- AI output is labelled as AI output. A mock is used only in tests and is always marked
  `MOCK`.
- We do not present an assumption (e.g. "what makes a project accepted") as a fact. Open
  assumptions are tracked in [OPEN_QUESTIONS](OPEN_QUESTIONS.md).

## 4. Secrets

- Secrets live **only** in environment variables, typed `pydantic.SecretStr`.
- `.env` and `*.db` are git-ignored. Only `.env.example` is committed and it contains
  **placeholders only**.
- Secrets must never appear in logs, error messages, tracebacks, Telegram messages or
  any committed file. The logging layer applies a redaction filter; `Settings.safe_dump()`
  returns a log-safe view.
- No real credentials are ever committed or pasted into code, tests, docs or fixtures.

## 5. Git safety

- The repository must be rooted at the project directory itself, never inside another
  repository (e.g. a home directory).
- Before **every** commit: run `git rev-parse --show-toplevel` (must print this project's
  path) and `git status --short`, and review the exact staged file list.
- No blind `git add .`. Stage explicit paths.
- Never force-push. Never commit secrets. Never commit local databases or virtualenvs.

## 6. Scope control

- Scope is a personal assistant for **one** freelance engineer. No web dashboard, no
  SaaS/multi-user, no payments, no platforms beyond Mostaql and Khamsat.
- Work proceeds in small, verifiable steps; each step is documented and committed on its
  own.
- Ideas outside the agreed scope are recorded in [FUTURE](FUTURE.md), not implemented.

## 7. Nothing client-pending is used until approved

The following are recorded in `config/profile.yaml` but are **inert** until the client
explicitly flips the flag. They must never influence matching or drafting before then:

| Item | Gate |
| ---- | ---- |
| Arabic keywords (`keywords_ar_proposed`) | `approved: true` per keyword |
| Unconfirmed skills (`unconfirmed_skills`) | `confirmed: true` per skill |
| Proposed tracks (`proposed_tracks`) | `approved: true` per track |
| Out-of-scope domains (`out_of_scope_domains`) | `approved: true` per domain |

Out-of-scope domains are a **soft** score penalty plus AI prompt guidance - **not** a hard
keyword exclusion.

## 8. Budget is never an exclusion criterion

A project's budget never causes exclusion. Budget fields may inform the human but never
disqualify a project on their own.
