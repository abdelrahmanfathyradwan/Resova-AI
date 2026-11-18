# SOURCES

How Resova learns about freelance opportunities, and the honest status of every source.

This document is referenced from the [README](../README.md); the binding compliance rules
live in [PROJECT_RULES](PROJECT_RULES.md).

## Permitted discovery channels

Only two channels are permitted. Both are things the client is entitled to read:

1. **The client's own platform notification emails** - the alert emails Mostaql/Khamsat
   send to the client's own mailbox. Read with the client's consent, via IMAP.
2. **Manual paste** of a project's text by the client (a Telegram channel, Phase 5).

There is **no scraping**, no bypassing of authentication / CAPTCHA / bot protection /
rate limits, no session cookies, no third-party scraping services, and Resova **never**
fetches a platform project URL by itself. Platform passwords are never requested or stored.

Neither Mostaql nor Khamsat offers an official public API for this purpose (verified), and
Mostaql runs bot protection, so the two channels above are the only viable ones.

## The source contract

Every source implements [`BaseSource`](../src/resova/sources/base.py), declared in
`src/resova/sources/base.py`:

| Member | Meaning |
| ------ | ------- |
| `platform` | The `Platform` this source belongs to. |
| `status` | Implementation status - one of the exact labels below. |
| `capabilities()` | Which `SourceKind` channels it genuinely handles. |
| `fetch_new(since)` | Returns items newer than `since` (`None` = everything available). |
| `normalize(raw)` | Maps a `RawProject` to the unified project model, or `None` if unusable. |
| `health_check()` | Cheap, side-effect-free readiness check that sends nothing. |

A source is side-effect free until `fetch_new` is called.

`RawProject` carries what a source received; every field except `source_kind` is optional
because alert emails are often partial. Missing values stay `None` and are never invented.
The unified project model is `UnifiedProject` (= `ProjectUpsert`), which matches the
`projects` table and is the exact type the repository consumes - one definition only.

## Status labels

These labels are quoted **verbatim** from `SourceStatus` and must never be softened:

| Label | Meaning |
| ----- | ------- |
| `Implemented` | Working, covered by tests, safe to enable. |
| `Not implemented - pending sample emails` | The interface exists; the concrete parser does not, because no real sample email has been supplied. |

## Source inventory

| Platform | Priority | Source | Status | Notes |
| -------- | -------- | ------ | ------ | ----- |
| Mostaql | 1 | alert-email source | **Not implemented - pending sample emails** | Field set, URL format and delivery latency are all unknown until real samples arrive (OQ-3). |
| Mostaql | 1 | manual-paste source | Not implemented - Phase 5 | Manual paste is delivered through the Telegram channel, which is a later phase. |
| Khamsat | 2 | alert-email source | **Not implemented - pending sample emails** | Same as Mostaql, plus: it is unknown whether Khamsat's alerts cover "community requests" at all (OQ-11). |
| Khamsat | 2 | manual-paste source | Not implemented - Phase 5 | Same as Mostaql. |

**Nothing about Mostaql or Khamsat is implemented today.** No parser, no credentials, no
network call to either platform exists in this repository. This is deliberate.

## Acquisition channels (`SourceKind`)

The database models three channels; only the first two are permitted:

| `SourceKind` | Permitted | Used by |
| ------------ | --------- | ------- |
| `alert_email` | Yes | The client's own notification mailbox. |
| `manual_paste` | Yes | The client pasting a project into Telegram. |
| `website_page` | **No** | Reserved for the future; would require access we do not have. Never populated today. |

## What is explicitly deferred

- **Concrete parsers** for the two email sources: `TODO(unverified)` until real sample
  emails are provided (OQ-2, OQ-3, OQ-4).
- **Platform-specific id extraction from URLs.** We do **not** assume any id format inside
  a Mostaql/Khamsat URL. Today a URL is only normalized (see `resova.utils.normalize`) and
  hashed for deduplication. Any id extraction waits for real emails: `TODO(unverified)`.
- **The manual-paste source**: Phase 5, together with the Telegram bot.
- **Latency measurement** on real data: blocked on real timestamps (OQ-13).

## Enabling a source (checklist)

Before a source may be marked `Implemented`, all of these must hold:

1. Real sample emails (or real pasted text) were supplied and are the basis of the parser.
2. The compliance question for that platform is closed (OQ-4).
3. Parsing is covered by tests built from the real samples (mocks only where marked `MOCK`).
4. Missing fields stay `null` and are surfaced to the human as *not provided*.
5. `status` is updated here, in the same commit.
