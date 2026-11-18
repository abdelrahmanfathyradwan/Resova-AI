# OPEN QUESTIONS

Items that are unresolved, unverified, or blocked on a decision. Each item names an
**owner**, a **status**, and **what it blocks**. Nothing here may be guessed at in code:
until a question is closed, the related capability stays `TODO` / stubbed.

## Owners

| Code | Who | Can resolve by |
| ---- | --- | -------------- |
| **client** | The freelance engineer Resova works for | Answering / approving |
| **me** | You (the project owner) | Verifying with real data, providing access, approving |
| **you** | The assistant engineer (me, building Resova) | Researching, proposing, implementing |

## Status legend

- `Open` - raised, nobody has answered yet.
- `Proposed` - a proposal exists and is awaiting approval.
- `TODO(unverified)` - assumed but not yet confirmed against reality.
- `Deciding` - options gathered, decision pending approval.
- `Blocked` - cannot progress until another item closes.

---

### OQ-1 - stdlib `imaplib` has no IDLE support

- **Owner:** you (propose) / me (approve)
- **Status:** Deciding - deferred to Phase 3
- **Question:** `imaplib` in the standard library cannot do IMAP IDLE, so new alert
  emails can only be discovered by polling. Choose between short-interval polling
  (30-60 s, zero extra dependencies) and adding a small IMAP library that supports
  IDLE (push).
- **Blocks:** Phase 3 email ingestion design (`EMAIL_POLL_MODE`, `POLL_INTERVAL_SECONDS`).

### OQ-2 - Gmail / Outlook IMAP auth constraints

- **Owner:** me (verify on the real mailbox)
- **Status:** TODO(unverified)
- **Question:** Gmail requires an App Password (or OAuth2) and has moved on from
  "less secure apps"; Outlook/Microsoft 365 has its own app-password / OAuth policy.
  We do not yet know which provider the client's alert mailbox will use, nor whether
  plain IMAP login will even be permitted.
- **Blocks:** Phase 3 authentication implementation and the exact `IMAP_*` variables.

### OQ-3 - Exact format, fields and latency of Mostaql / Khamsat alert emails

- **Owner:** me (supply real sample emails)
- **Status:** TODO(unverified)
- **Question:** We have not seen a real alert email from either platform. Unknown:
  the field set (title, budget, duration, competition count, URL), whether the body is
  HTML or plain text, the project URL format, and the delivery latency between a
  project being posted and the alert arriving.
- **Blocks:** Phase 3 email parser and the latency metric (needs real timestamps).

### OQ-4 - Platform terms on automated processing of their own emails

- **Owner:** me / client (account holders)
- **Status:** TODO(unverified)
- **Question:** Mostaql and Khamsat terms have not been reviewed for whether
  programmatically reading/processing the notification emails they send to the
  client's own inbox is permitted. This is a compliance gate, not a technical one.
- **Blocks:** Phase 3 go/no-go. If not permitted, the manual-paste channel is the
  fallback.

### OQ-5 - Client confirmation of the six unconfirmed skills

- **Owner:** client
- **Status:** Open
- **Question:** The signed proposal names six skills that are NOT in the client
  profile: Architectural Design, Structural Drawings, Electrical & Plumbing Drawings,
  Quantity Surveying, PDF & Document Processing, Professional Documents. The client
  must confirm which (if any) are actually offered.
- **Blocks:** Phase 4 matching scope. In `config/profile.yaml` these live under
  `unconfirmed_skills` with `confirmed: false` and are excluded from matching and
  drafting until the client flips them to `confirmed: true`.

### OQ-6 - Approved Arabic keywords

- **Owner:** client (approve) / you (proposed the list)
- **Status:** Proposed
- **Question:** Arabic keywords were not supplied, so the assistant proposed a starter
  list per track. They are stored under `keywords_ar_proposed` with `approved: false`
  and are NOT used for matching until the client sets `approved: true`.
- **Blocks:** Phase 4 Arabic-language filtering.

### OQ-7 - Notification language

- **Owner:** client
- **Status:** Open - current default is `ar` (`settings.notify_language` in
  `config/profile.yaml`)
- **Question:** Confirm the language of Telegram notifications (Arabic, English, or
  bilingual). Default assumed Arabic.
- **Blocks:** Phase 5 Telegram message rendering.

### OQ-8 - Excluded keywords

- **Owner:** client
- **Status:** Open - currently `excluded_keywords: []` in `config/profile.yaml`
- **Question:** Any keywords that must always disqualify a project (e.g. unpaid work,
  equity-only, "test task for free")? None supplied yet.
- **Blocks:** Phase 4 filtering (negative rules).
