"""games-career.com scraper — HTML/microdata.

games-career.com is a German-headquartered job board for the games
industry, with strong DACH (Germany/Austria/Switzerland) coverage but
also UK, FR, NL, US, and remote roles. Clean Schema.org microdata on
every listing — easiest possible parse.

DOM shape (locked-in 2026-04-18):
    <div class="joblist_element_title">
      <h3 itemprop="title"><a href="…/Joboffer/<id>_<slug>">Title</a></h3>
      <time itemprop="datePosted" datetime="YYYY-MM-DD">…</time>
      <div class="description">
        <td itemprop="hiringOrganization">
          <a><span itemprop="name">Company</span></a>
        </td>
        <td itemprop="jobLocation">
          <span itemprop="address">
            <span itemprop="addressLocality">City</span> /
            <span itemprop="addressCountry">Country</span>
          </span>
        </td>
      </div>
    </div>

Pagination: `?page=2`, `?page=3`, … on the SAME path the listing lives at
(`/Joboffers` for page 1, `/Joboffer/?page=N` for paginated). Each page
has 15 cards.

We default to 4 pages (60 jobs) — the volume past page 4 is mostly
older rolling listings re-surfaced.
"""
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.sources_config import load_source_config
from scrapers.user_agent import USER_AGENT as _USER_AGENT


_WHITESPACE_RE = re.compile(r"\s+")
_ID_RE = re.compile(r"/Joboffer/(\d+)")


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


def _itemprop_text(soup: BeautifulSoup, prop: str) -> str:
    """Find the first element with `itemprop=<prop>` and return its
    cleaned text. Returns "" when not found so callers can `or None`."""
    node = soup.find(attrs={"itemprop": prop})
    if node is None:
        return ""
    return _clean(node.get_text(" ", strip=True))


@register("games_career")
class GamesCareerScraper(BaseScraper):
    source_name = "games_career"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily, with HTML batch
    rate_limit_rps = 1.0                 # Polite — German sites are sensitive

    BASE_URL  = "https://www.games-career.com"
    LIST_URL  = "https://www.games-career.com/Joboffers"
    PAGED_URL = "https://www.games-career.com/Joboffer/?page={page}"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    DEFAULT_MAX_PAGES = 4   # 4 * 15 = 60 jobs per run

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        max_pages = int(cfg.get("max_pages") or self.DEFAULT_MAX_PAGES)

        seen_ids: set[str] = set
        for page in range(1, max_pages + 1):
            url = self.LIST_URL if page == 1 else self.PAGED_URL.format(page=page)
            self._throttle
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": self.USER_AGENT, "Accept": "text/html"},
                    timeout=30,
                )
                resp.raise_for_status
            except requests.RequestException:
                # Stop pagination on network blip; don't fail the run.
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all(class_="joblist_element_title")
            if not cards:
                # Past the end of pagination.
                break

            new_count = 0
            for card in cards:
                href_tag = card.find("a", href=True, attrs={"itemprop": False})
                # Fall back to the title <h3>'s anchor if needed.
                if href_tag is None:
                    title_tag = card.find(["h3", "h2"])
                    href_tag = title_tag.find("a", href=True) if title_tag else None
                if not href_tag:
                    continue
                href = href_tag.get("href") or ""
                m = _ID_RE.search(href)
                if not m:
                    continue
                native_id = m.group(1)
                if native_id in seen_ids:
                    continue
                seen_ids.add(native_id)
                new_count += 1
                yield {
                    "_native_id": native_id,
                    "_href":      href,
                    "_url":       urljoin(self.BASE_URL, href),
                    "_html":      str(card),
                }

            if new_count == 0:
                # Page returned only repeats — pagination has wrapped.
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        native_id = payload.get("_native_id") or ""
        url       = payload.get("_url") or ""
        if not native_id or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # Schema.org microdata is the primary source — the site has been
        # consistent about it for years. We add fallbacks but they rarely fire.
        title = _itemprop_text(soup, "title")
        if not title:
            # Fallback: title is the anchor text inside the <h3>.
            h = soup.find(["h3", "h2"])
            if h:
                title = _clean(h.get_text(" ", strip=True))

        company = ""
        co_node = soup.find(attrs={"itemprop": "hiringOrganization"})
        if co_node:
            name_node = co_node.find(attrs={"itemprop": "name"})
            company = _clean(name_node.get_text(" ", strip=True)) if name_node else \
                      _clean(co_node.get_text(" ", strip=True))

        # Build "City, Country" from the addressLocality + addressCountry
        # microdata children. Falls back to the raw jobLocation text.
        location = None
        loc_node = soup.find(attrs={"itemprop": "jobLocation"})
        if loc_node:
            city    = _itemprop_text(loc_node, "addressLocality")
            country = _itemprop_text(loc_node, "addressCountry")
            if city and country:
                location = f"{city}, {country}"
            elif city:
                location = city
            elif country:
                location = country
            else:
                location = _clean(loc_node.get_text(" ", strip=True)) or None

        # Posted-at: <time datetime="YYYY-MM-DD"> attribute is ISO-clean.
        posted_at = None
        time_node = soup.find("time", attrs={"itemprop": "datePosted"})
        if time_node and time_node.get("datetime"):
            # Append T00:00:00Z so canonicalize_posted_at parses it cleanly.
            posted_at = time_node["datetime"] + "T00:00:00Z"

        if not title or not company:
            return None

        # Remote inference: location string contains "remote" (e.g. when the
        # site marks roles as "Worldwide / Remote"). Otherwise leave None.
        remote = None
        if location and "remote" in location.lower:
            remote = True

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,           # Card has no JD body
            posted_at=posted_at,
            remote=remote,
            raw=payload,
        )
