# FUTURE

Ideas that are **out of scope now**. Nothing here is implemented, nothing here is promised,
and nothing here may be built without a new decision. Recording an idea costs nothing;
implementing one inside an unrelated step is scope creep.

Scope is fixed by [PROJECT_RULES](PROJECT_RULES.md) section 6: a personal assistant for
**one** freelance engineer, on Mostaql and Khamsat only.

## Explicitly out of scope (not "later", but "not this project")

| Idea | Why it is out |
| ---- | ------------- |
| Web dashboard / GUI | A Telegram bot plus a CLI is the agreed interface. |
| SaaS / multi-user / accounts | The product is single-user by design; this would change the security model completely. |
| Payments, invoicing, subscriptions | Never part of the brief. |
| Platforms other than Mostaql and Khamsat | Each platform is a new compliance question. |
| Automated proposal **submission** | No auto-apply, ever ([PROJECT_RULES](PROJECT_RULES.md) section 2). |
| Scraping, headless browsers, proxy scrapers, session-cookie replay | Compliance ([PROJECT_RULES](PROJECT_RULES.md) section 1). |
| Storing platform passwords | The system never has an account on the platform. |
| Estimating a project's competition level when the source did not provide it | Fabrication ([PROJECT_RULES](PROJECT_RULES.md) section 3). |

## Possible later, but needs its own approval

| Idea | Note |
| ---- | ---- |
| PostgreSQL instead of SQLite | Already a configuration change only (D-2); no code change is expected, but it needs a decision and a test run against Postgres. |
| IMAP `IDLE` push instead of polling | Needs a dependency that supports IDLE, or a short polling interval. See OQ-1. |
| Automatic platform-id extraction from project URLs | Deliberately deferred until real alert emails prove the URL format: `TODO(unverified)`. |
| Semantic (embedding) similarity for near-duplicate projects | Today's dedup is exact-hash; embeddings would be a new cost and a new failure mode. |
| Retrieval-augmented proposal drafting from past winning proposals | Would need real outcome data first. |
| A weekly digest / performance report | Nice-to-have; needs data that does not exist yet. |
| Multi-language UI (English notifications) | Currently Arabic, see OQ-7. |
| Docker packaging | The tool runs locally with a virtualenv; containers add nothing yet. |
| A 4th matching track (architectural drafting / CAD / BOQ) | Proposed, **not approved** - see OQ-9. |
| Calibrating the score rubric against the labeled dataset | Blocked until the acceptance criteria are confirmed - see OQ-10. |

## How to add something from this list

1. Raise it as an item in [OPEN_QUESTIONS](OPEN_QUESTIONS.md) if it is not already there.
2. Get an explicit approval (and, if it touches compliance, a compliance answer first).
3. Add a decision to [DECISIONS](DECISIONS.md) explaining the change.
4. Only then write code - in a small, verifiable, separately committed step.
