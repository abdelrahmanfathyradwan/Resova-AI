"""Public listing poller source (Phase 1 / Step 2.7).

Implements `BaseSource` for public listing pages of Mostaql and Khamsat.
The poller respects the configuration flags defined in `resova.config.settings`:

- `ENABLE_LISTING_POLLER` – master switch (default **False**).
- `LISTING_POLLER_ACK` – explicit risk-acknowledgement flag.
- `MOSTAQL_LISTING_URLS` / `KHAMSAT_LISTING_URLS` – one-or-more URLs per platform.
- `LISTING_POLL_INTERVAL_SECONDS` – minimum interval (enforced >= 60 s).
- `LISTING_POLL_JITTER_SECONDS` – optional jitter added to the interval.
- `USER_AGENT_CONTACT` – contact e-mail placed in the User-Agent string.

Persistent deduplication and baseline handling:
- Reads already-known projects from the database so restarts do not re-notify duplicates.
- Supports independent toggling of Mostaql (`enable_mostaqel`) and Khamsat (`enable_khamsat`).
- Bounded exponential backoff on HTTP 429/403 or network errors.
- Never fetches individual project pages — only public listing pages.
"""

from __future__ import annotations

import contextlib
import logging
import random
import re
from collections.abc import Sequence
from datetime import datetime

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from resova.config.settings import Settings, get_settings
from resova.db.models import Platform, Project, SourceKind
from resova.db.repositories import ProjectUpsert
from resova.sources.base import (
    BaseSource,
    RawProject,
    SourceCapabilities,
    SourceStatus,
    UnifiedProject,
)
from resova.utils.normalize import normalize_url

log = logging.getLogger(__name__)


class _MostaqlParser:
    @staticmethod
    def parse(html: str) -> list[RawProject]:
        soup = BeautifulSoup(html, "html.parser")
        projects: list[RawProject] = []
        for card in soup.select("div.project-card, div.project_row, tr.project-row"):
            link = card.select_one("a.title, h2.project-title a, a[href*='/project/']")
            if not link:
                # Fallback: any link with /project/ in href
                link = card.select_one("a[href*='/projects/'], a[href*='/project/']")
            if not link or not (href := link.get("href")):
                continue

            external_id = None
            match = re.search(r"/projects?/(\d+)", str(href))
            if match:
                external_id = match.group(1)
            else:
                for part in str(href).split("/"):
                    if part.isdigit():
                        external_id = part
                        break

            title = link.get_text(strip=True) or None
            category_el = card.select_one("span.category, td.category, a.category")
            category = category_el.get_text(strip=True) if category_el else None

            desc_el = card.select_one("p.project-brief, div.project-details, td.details")
            description = desc_el.get_text(strip=True) if desc_el else None

            projects.append(
                RawProject(
                    source_kind=SourceKind.website_page,
                    title=title,
                    description=description,
                    url=str(href),
                    external_id=external_id,
                    extras={"category": category, "platform": Platform.mostaql},
                )
            )
        return projects


class _KhamsatParser:
    @staticmethod
    def parse(html: str) -> list[RawProject]:
        soup = BeautifulSoup(html, "html.parser")
        projects: list[RawProject] = []
        for row in soup.select("tr.request-row, div.request-card, tr.forum_post"):
            link = row.select_one("a.title, h3 a, a[href*='/community/requests/']")
            if not link:
                link = row.select_one("a[href*='/requests/'], a[href*='/community/']")
            if not link or not (href := link.get("href")):
                continue

            external_id = None
            if id_attr := row.get("data-id"):
                external_id = str(id_attr).strip()
            else:
                for part in str(href).split("/"):
                    if part.isdigit():
                        external_id = part
                        break

            title = link.get_text(strip=True) or None
            cat_el = row.select_one("td.category, span.category")
            category = cat_el.get_text(strip=True) if cat_el else None

            desc_el = row.select_one("p.details, td.details")
            description = desc_el.get_text(strip=True) if desc_el else None

            projects.append(
                RawProject(
                    source_kind=SourceKind.website_page,
                    title=title,
                    description=description,
                    url=str(href),
                    external_id=external_id,
                    extras={"category": category, "platform": Platform.khamsat},
                )
            )
        return projects


class ListingPoller(BaseSource):
    """Poll public listing pages for new freelance opportunities.

    The poller is **disabled by default**.
    Requires ``ENABLE_LISTING_POLLER=true`` and ``LISTING_POLLER_ACK=true``.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        session: Session | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session = session
        self._client = client or httpx.Client(timeout=10.0, follow_redirects=True)
        self._known_keys: set[str] = set()
        self._baseline_done: bool = False
        self._backoff: dict[Platform, int] = {Platform.mostaql: 0, Platform.khamsat: 0}

        # Initialize known keys from persistent DB if session available
        if self._session is not None:
            self._load_known_from_db()

    def _load_known_from_db(self) -> None:
        """Load previously recorded projects from database to prevent duplicates on restart."""
        if self._session is None:
            return
        try:
            stmt = select(Project.url, Project.external_id, Project.platform)
            for url, ext_id, platform in self._session.execute(stmt):
                if url:
                    self._known_keys.add(f"url:{url}")
                if ext_id:
                    self._known_keys.add(f"ext:{platform}:{ext_id}")
            self._baseline_done = True
            log.info(
                "Listing poller initialized %d known keys from database.", len(self._known_keys)
            )
        except Exception as exc:
            log.warning("Could not pre-load known projects from database: %s", exc)

    @property
    def platform(self) -> Platform:
        return Platform.manual

    @property
    def status(self) -> SourceStatus:
        return SourceStatus.implemented

    @property
    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            platform=Platform.manual,
            supported_kinds=frozenset({SourceKind.website_page}),
        )

    def _request(self, url: str) -> httpx.Response | None:
        headers = {"User-Agent": f"ResovaBot/0.1 (+{self._settings.user_agent_contact})"}
        try:
            resp = self._client.get(url, headers=headers)
            if resp.status_code in (403, 429):
                log.warning("Listing source blocked (status %s) for %s", resp.status_code, url)
                return None
            resp.raise_for_status()
            return resp
        except httpx.HTTPError as exc:
            log.warning("Network error while fetching %s: %s", url, exc)
            return None

    def fetch_new(self, since: datetime | None = None) -> Sequence[RawProject]:
        """Fetch newly discovered projects from the configured listing URLs."""
        if not self._settings.enable_listing_poller or not self._settings.listing_poller_ack:
            log.info("Listing poller is disabled or unacknowledged.")
            return []

        all_raw: list[RawProject] = []
        settings = self._settings

        def _get_urls(field_val: object) -> list[str]:
            if isinstance(field_val, list):
                return [str(u).strip() for u in field_val if str(u).strip()]
            if isinstance(field_val, str) and field_val.strip():
                return [u.strip() for u in field_val.split(",") if u.strip()]
            return []

        # Process Mostaql if enabled
        enable_mostaql = getattr(settings, "enable_mostaqel", True) and getattr(
            settings, "enable_mostaql", True
        )
        if enable_mostaql:
            self._process_platform(
                _get_urls(settings.mostaql_listing_urls),
                Platform.mostaql,
                _MostaqlParser,
                all_raw,
            )

        # Process Khamsat if enabled
        if getattr(settings, "enable_khamsat", True):
            self._process_platform(
                _get_urls(settings.khamsat_listing_urls),
                Platform.khamsat,
                _KhamsatParser,
                all_raw,
            )

        # Baseline handling on fresh start without DB
        if not self._baseline_done:
            for raw in all_raw:
                self._record_known(raw)
            self._baseline_done = True
            log.info(
                "Listing poller baseline completed – %d projects cached.", len(self._known_keys)
            )
            return []

        # Filter genuinely new projects
        new_projects: list[RawProject] = []
        for raw in all_raw:
            if not self._is_known(raw):
                self._record_known(raw)
                new_projects.append(raw)

        return new_projects

    def _process_platform(
        self,
        urls: list[str],
        platform: Platform,
        parser: type[_MostaqlParser] | type[_KhamsatParser],
        collector: list[RawProject],
    ) -> None:
        if not urls:
            return
        if self._backoff[platform] > 0:
            log.info(
                "Skipping %s polling due to back-off (%s s)",
                platform.value,
                self._backoff[platform],
            )
            self._backoff[platform] = max(
                0, self._backoff[platform] - self._settings.listing_poll_interval_seconds
            )
            return

        for url in urls:
            resp = self._request(url)
            if resp is None:
                self._backoff[platform] = min(self._backoff[platform] * 2 + 10, 300)
                continue

            projects = parser.parse(resp.text)
            for proj in projects:
                if proj.url:
                    with contextlib.suppress(Exception):
                        proj.url = str(httpx.URL(url).join(proj.url))
            collector.extend(projects)

    def _is_known(self, raw: RawProject) -> bool:
        if raw.url and f"url:{raw.url}" in self._known_keys:
            return True
        key = f"ext:{raw.extras.get('platform')}:{raw.external_id}"
        return bool(raw.external_id and key in self._known_keys)

    def _record_known(self, raw: RawProject) -> None:
        if raw.url:
            self._known_keys.add(f"url:{raw.url}")
        if raw.external_id:
            self._known_keys.add(f"ext:{raw.extras.get('platform')}:{raw.external_id}")

    def normalize(self, raw: RawProject) -> UnifiedProject | None:
        """Convert RawProject into UnifiedProject (ProjectUpsert)."""
        if not raw.title:
            return None

        platform_val = raw.extras.get("platform", Platform.manual)
        if isinstance(platform_val, Platform):
            platform = platform_val
        elif raw.url and "mostaql.com" in raw.url:
            platform = Platform.mostaql
        elif raw.url and "khamsat.com" in raw.url:
            platform = Platform.khamsat
        else:
            platform = Platform.manual

        return ProjectUpsert(
            platform=platform,
            source_kind=raw.source_kind,
            external_id=raw.external_id,
            url=normalize_url(raw.url),
            title=raw.title,
            description=raw.description,
            category=raw.extras.get("category"),
        )

    def health_check(self) -> bool:
        has_urls = bool(self._settings.mostaql_listing_urls or self._settings.khamsat_listing_urls)
        return has_urls

    def schedule_next(self) -> int:
        """Return the number of seconds until the next poll with jitter."""
        base = self._settings.listing_poll_interval_seconds
        jitter = random.randint(0, self._settings.listing_poll_jitter_seconds)
        return base + jitter
