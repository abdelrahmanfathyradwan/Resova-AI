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
import urllib.parse
from collections.abc import Sequence
from datetime import datetime, timedelta

import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from resova.config.settings import Settings, get_settings
from resova.db.base import utcnow
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

MOSTAQL_PROJECT_RE = re.compile(
    r"^(?:https?://(?:www\.)?mostaql\.com)?/projects?/(\d+)(?:-([^/?#\s]*))?",
    re.IGNORECASE,
)
KHAMSAT_PROJECT_RE = re.compile(
    r"^(?:https?://(?:www\.)?khamsat\.com)?/(?:community/)?requests?/(\d+)(?:-([^/?#\s]*))?",
    re.IGNORECASE,
)


def _clean_slug_title(slug: str | None) -> str | None:
    """Clean and unquote URL slug into human-readable project title."""
    if not slug:
        return None
    try:
        clean = urllib.parse.unquote(slug).replace("-", " ").strip()
        return clean if len(clean) >= 3 else None
    except Exception:
        return None


def _parse_khamsat_date(title_str: str) -> datetime | None:
    """Parse Khamsat timestamp string, e.g. '22/11/2026 13:34:46 GMT'."""
    try:
        clean = title_str.replace("GMT", "").strip()
        dt = datetime.strptime(clean, "%d/%m/%Y %H:%M:%S")
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def _parse_mostaql_date(dt_str: str) -> datetime | None:
    """Parse Mostaql ISO timestamp string, e.g. '2026-11-23 08:39:10'."""
    try:
        dt = datetime.fromisoformat(dt_str.strip().replace("Z", ""))
        return dt.replace(tzinfo=None)
    except Exception:
        return None


def _parse_relative_arabic_date(text: str, now: datetime | None = None) -> datetime | None:
    """Parse relative Arabic time string, e.g. 'منذ 3 ساعات' or 'منذ 9 أيام'."""
    now = now or utcnow()
    text = text.strip()
    if m := re.search(r"منذ\s+(\d+)\s+دقيق", text):
        return now - timedelta(minutes=int(m.group(1)))
    if m := re.search(r"منذ\s+(\d+)\s+ساع", text):
        return now - timedelta(hours=int(m.group(1)))
    if "منذ ساعة" in text:
        return now - timedelta(hours=1)
    if "منذ ساعتين" in text:
        return now - timedelta(hours=2)
    if m := re.search(r"منذ\s+(\d+)\s+أيام", text):
        return now - timedelta(days=int(m.group(1)))
    if m := re.search(r"منذ\s+(\d+)\s+يوم", text):
        return now - timedelta(days=int(m.group(1)))
    if "منذ يومين" in text:
        return now - timedelta(days=2)
    if "منذ يوم" in text:
        return now - timedelta(days=1)
    return None


class _MostaqlParser:
    @staticmethod
    def parse(html: str) -> list[RawProject]:
        soup = BeautifulSoup(html, "html.parser")
        projects: list[RawProject] = []
        seen_ids: set[str] = set()

        # Tier 1: Detailed card extraction
        card_sel = "div.project-card, div.project_row, tr.project-row, div.project--item"
        for card in soup.select(card_sel):
            link = card.select_one("a.title, h2.project-title a, a[href*='/project/']")
            if not link:
                link = card.select_one("a[href*='/projects/'], a[href*='/project/']")
            if not link or not (href := link.get("href")):
                continue

            external_id = None
            match = MOSTAQL_PROJECT_RE.search(str(href))
            if match:
                external_id = match.group(1)
            else:
                for part in str(href).split("/"):
                    if part.isdigit():
                        external_id = part
                        break

            if external_id:
                seen_ids.add(external_id)

            title = link.get_text(strip=True) or None
            category_el = card.select_one("span.category, td.category, a.category")
            category = category_el.get_text(strip=True) if category_el else None

            desc_el = card.select_one("p.project-brief, div.project-details, td.details")
            description = desc_el.get_text(strip=True) if desc_el else None

            # Extract published date
            published_at = None
            time_el = card.select_one("time[datetime], time[title], time")
            if time_el:
                dt_str = time_el.get("datetime") or time_el.get("title")
                if dt_str:
                    published_at = _parse_mostaql_date(str(dt_str))
                if not published_at:
                    published_at = _parse_relative_arabic_date(time_el.get_text(strip=True))

            href_str = str(href).strip()
            if href_str.startswith("/"):
                project_url = f"https://mostaql.com{href_str}"
            elif not href_str.startswith("http"):
                project_url = f"https://mostaql.com/{href_str.lstrip('/')}"
            else:
                project_url = href_str

            projects.append(
                RawProject(
                    source_kind=SourceKind.website_page,
                    title=title,
                    description=description,
                    url=project_url,
                    external_id=external_id,
                    published_at=published_at,
                    extras={"category": category, "platform": Platform.mostaql},
                )
            )

        # Tier 2: Universal anchor fallback scan
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).strip()
            m = MOSTAQL_PROJECT_RE.search(href)
            if not m:
                continue
            ext_id = m.group(1)
            if ext_id in seen_ids:
                continue
            seen_ids.add(ext_id)

            link_text = a.get_text(strip=True)
            slug = m.group(2)
            slug_title = _clean_slug_title(slug)

            title = (
                link_text
                if len(link_text) >= 3
                else (slug_title or f"مشروع مستقل #{ext_id}")
            )

            clean_url = f"https://mostaql.com/project/{ext_id}"
            if slug:
                clean_url = f"{clean_url}-{slug}"

            projects.append(
                RawProject(
                    source_kind=SourceKind.website_page,
                    title=title,
                    description=None,
                    url=clean_url,
                    external_id=ext_id,
                    published_at=None,
                    extras={"category": None, "platform": Platform.mostaql},
                )
            )

        return projects


class _KhamsatParser:
    @staticmethod
    def parse(html: str) -> list[RawProject]:
        soup = BeautifulSoup(html, "html.parser")
        projects: list[RawProject] = []
        seen_ids: set[str] = set()

        # Tier 1: Detailed row/card extraction
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
                match = KHAMSAT_PROJECT_RE.search(str(href))
                if match:
                    external_id = match.group(1)
                else:
                    for part in str(href).split("/"):
                        if m := re.search(r"^(\d+)", part):
                            external_id = m.group(1)
                            break

            if external_id:
                seen_ids.add(external_id)

            title = link.get_text(strip=True) or None
            cat_el = row.select_one("td.category, span.category")
            category = cat_el.get_text(strip=True) if cat_el else None

            desc_el = row.select_one("p.details, td.details")
            description = desc_el.get_text(strip=True) if desc_el else None

            # Extract published date and last activity
            published_at = None
            last_activity_at = None

            for span in row.select("span[title], li span"):
                txt = span.get_text(strip=True)
                title_attr = span.get("title")
                if "آخر تفاعل" in txt:
                    if title_attr:
                        last_activity_at = _parse_khamsat_date(str(title_attr))
                    if not last_activity_at:
                        last_activity_at = _parse_relative_arabic_date(txt)
                elif "منذ" in txt and not published_at:
                    if title_attr:
                        published_at = _parse_khamsat_date(str(title_attr))
                    if not published_at:
                        published_at = _parse_relative_arabic_date(txt)

            href_str = str(href).strip()
            if href_str.startswith("/"):
                project_url = f"https://khamsat.com{href_str}"
            elif not href_str.startswith("http"):
                project_url = f"https://khamsat.com/{href_str.lstrip('/')}"
            else:
                project_url = href_str

            projects.append(
                RawProject(
                    source_kind=SourceKind.website_page,
                    title=title,
                    description=description,
                    url=project_url,
                    external_id=external_id,
                    published_at=published_at,
                    last_activity_at=last_activity_at,
                    extras={"category": category, "platform": Platform.khamsat},
                )
            )

        # Tier 2: Universal anchor fallback scan
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).strip()
            m = KHAMSAT_PROJECT_RE.search(href)
            if not m:
                continue
            ext_id = m.group(1)
            if ext_id in seen_ids:
                continue
            seen_ids.add(ext_id)

            link_text = a.get_text(strip=True)
            slug = m.group(2)
            slug_title = _clean_slug_title(slug)

            title = (
                link_text
                if len(link_text) >= 3
                else (slug_title or f"طلب خمسات #{ext_id}")
            )

            clean_url = f"https://khamsat.com/community/requests/{ext_id}"
            if slug:
                clean_url = f"{clean_url}-{slug}"

            projects.append(
                RawProject(
                    source_kind=SourceKind.website_page,
                    title=title,
                    description=None,
                    url=clean_url,
                    external_id=ext_id,
                    published_at=None,
                    last_activity_at=None,
                    extras={"category": None, "platform": Platform.khamsat},
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
        self._max_id: dict[Platform, int] = {Platform.mostaql: 0, Platform.khamsat: 0}
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
                    self._known_keys.add(f"url:{normalize_url(url)}")
                if ext_id:
                    self._known_keys.add(f"ext:{platform}:{ext_id}")
                    if ext_id.isdigit() and platform in self._max_id:
                        self._max_id[platform] = max(self._max_id[platform], int(ext_id))
            self._baseline_done = True
            log.info(
                "Listing poller initialized %d known keys from database (max_ids=%s).",
                len(self._known_keys),
                self._max_id,
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

        max_pages = max(1, min(getattr(self._settings, "max_poll_pages", 1), 5))

        for base_url in urls:
            # Ensure Mostaql sort=latest if missing
            url = base_url
            if platform == Platform.mostaql and "sort=" not in url:
                sep = "&" if "?" in url else "?"
                url = f"{url}{sep}sort=latest"

            for page in range(1, max_pages + 1):
                if page == 1:
                    page_url = url
                else:
                    if "page=" in url:
                        page_url = re.sub(r"([?&])page=\d+", rf"\g<1>page={page}", url)
                    else:
                        sep = "&" if "?" in url else "?"
                        page_url = f"{url}{sep}page={page}"

                resp = self._request(page_url)
                if resp is None:
                    self._backoff[platform] = min(self._backoff[platform] * 2 + 10, 300)
                    break

                projects = parser.parse(resp.text)
                if not projects:
                    break

                for proj in projects:
                    proj.extras["page"] = page
                    proj.extras["is_post_baseline"] = self._baseline_done
                    if proj.url:
                        with contextlib.suppress(Exception):
                            proj.url = str(httpx.URL(page_url).join(proj.url))
                collector.extend(projects)

                # Stop pagination early if all projects on current page are already known
                if self._baseline_done and all(self._is_known(p) for p in projects):
                    break

    def _is_known(self, raw: RawProject) -> bool:
        norm_url = normalize_url(raw.url) if raw.url else None
        if norm_url and f"url:{norm_url}" in self._known_keys:
            return True
        key = f"ext:{raw.extras.get('platform')}:{raw.external_id}"
        return bool(raw.external_id and key in self._known_keys)

    def _record_known(self, raw: RawProject) -> None:
        norm_url = normalize_url(raw.url) if raw.url else None
        if norm_url:
            self._known_keys.add(f"url:{norm_url}")
        if raw.external_id:
            plat = raw.extras.get("platform")
            self._known_keys.add(f"ext:{plat}:{raw.external_id}")
            if raw.external_id.isdigit() and isinstance(plat, Platform) and plat in self._max_id:
                self._max_id[plat] = max(self._max_id[plat], int(raw.external_id))

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

        url_val = raw.url
        if url_val:
            url_str = str(url_val).strip()
            if url_str.startswith("/"):
                if platform == Platform.khamsat:
                    url_str = f"https://khamsat.com{url_str}"
                elif platform == Platform.mostaql:
                    url_str = f"https://mostaql.com{url_str}"
            norm_url = normalize_url(url_str)
        else:
            norm_url = None

        page_num = raw.extras.get("page", 1)
        source_section = f"page:{page_num}"
        if raw.extras.get("is_post_baseline"):
            source_section = f"{source_section}:post_baseline"

        return ProjectUpsert(
            platform=platform,
            source_kind=raw.source_kind,
            external_id=raw.external_id,
            url=norm_url,
            title=raw.title,
            description=raw.description,
            category=raw.extras.get("category"),
            source_section=source_section,
            published_at=raw.published_at,
            last_activity_at=raw.last_activity_at,
        )

    def health_check(self) -> bool:
        has_urls = bool(self._settings.mostaql_listing_urls or self._settings.khamsat_listing_urls)
        return has_urls

    def schedule_next(self) -> int:
        """Return the number of seconds until the next poll with jitter."""
        base = self._settings.listing_poll_interval_seconds
        jitter = random.randint(0, self._settings.listing_poll_jitter_seconds)
        return base + jitter
