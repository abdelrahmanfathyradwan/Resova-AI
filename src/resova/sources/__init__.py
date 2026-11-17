"""Opportunity sources (Mostaql priority 1, Khamsat priority 2).

NOT IMPLEMENTED - Phase 1 / Step 2.7 (source interface design only).

Planned content:
- ``BaseSource`` protocol: ``capabilities()``, ``fetch_new(since)``, ``normalize(raw)``,
  ``health_check()``, plus the unified project model matching the database schema.

Compliance: discovery is limited to the client's own platform notification emails and
manual paste of project text. No scraping, no bypassing of authentication, bot
protection or rate limits, no session cookies, no third-party scraping services and
no automatic fetching of platform project URLs.
"""

__all__: list[str] = []
