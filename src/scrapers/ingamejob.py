"""ingamejob.com scraper — HTML.

ingamejob.com is a Ukraine-headquartered gaming-industry job board with
strong CIS/EU coverage and a growing US/global slice. Higher posting
volume than the Western boards but heavily skewed toward IC roles
(2D Artist, 3D Generalist, Unity Programmer); the algo gates will filter
those down.

DOM shape (locked-in 2026-04-18, Bootstrap-4 + LineAwesome icons):

    <div class="employer-job-listing-single">       (or +" premium-job")
      <div class="job-listing-company-logo">
        <a href="https://ingamejob.com/en/job/<slug>"></a>
      </div>
      <div class="listing-job-info container">
        <h5><a href="…/job/<slug>">Title</a></h5>
        <p><strong><i class="la la-building-o"></i> Company Name</strong></p>
        <p><i class="la la-map-marker"></i> Location</p>      (skipped on premium cards)
        <p><i class="la la-clock-o"></i> Posted N hours ago</p>
        <p><i class="la la-briefcase"></i> Full time / Part time</p>
      </div>
    </div>

Premium cards omit location, posted-at, and contract-type — only
title + company are guaranteed. We still emit those (the algo can
score on title alone) but flag them so the rationale shows it was
sparse data.

Pagination: `?page=N` on the same listing URL. 30 cards per page.
We default to 4 pages (~120 jobs) — enough for a daily filter pass.
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
_SLUG_RE = re.compile(r"/job/([a-z0-9\-_]+)", re.I)


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


def _icon_text(soup: BeautifulSoup, icon_class: str) -> str:
    """Find a <p> that contains an <i class="… <icon_class> …"> child
    and return the <p>'s text (minus the icon's empty content).

    Returns "" when no such row exists. Used to extract location, time,
    contract-type, etc. from the icon-prefixed lines.
    """
    icon = soup.find("i", class_=re.compile(rf"\b{re.escape(icon_class)}\b"))
    if icon is None:
        return ""
    p = icon.find_parent("p") or icon.parent
    if p is None:
        return ""
    return _clean(p.get_text(" ", strip=True))


@register("ingamejob")
class InGameJobScraper(BaseScraper):
    source_name = "ingamejob"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily, with HTML batch
    rate_limit_rps = 1.0

    BASE_URL  = "https://ingamejob.com"
    LIST_URL  = "https://ingamejob.com/en/jobs"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    DEFAULT_MAX_PAGES = 4   # 4 * 30 = 120 jobs per run

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        max_pages = int(cfg.get("max_pages") or self.DEFAULT_MAX_PAGES)

        seen_slugs: set[str] = set
        for page in range(1, max_pages + 1):
            url = self.LIST_URL if page == 1 else f"{self.LIST_URL}?page={page}"
            self._throttle
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": self.USER_AGENT, "Accept": "text/html"},
                    timeout=30,
                )
                resp.raise_for_status
            except requests.RequestException:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all(class_="employer-job-listing-single")
            if not cards:
                # Past the end of pagination.
                break

            new_count = 0
            for card in cards:
                # Title anchor sits inside the <h5>; either its href works.
                title_anchor = None
                h5 = card.find("h5")
                if h5:
                    title_anchor = h5.find("a", href=True)
                if title_anchor is None:
                    title_anchor = card.find("a", href=True)
                if title_anchor is None:
                    continue
                href = title_anchor.get("href") or ""
                m = _SLUG_RE.search(href)
                if not m:
                    continue
                slug = m.group(1)
                if slug in seen_slugs:
                    continue
                seen_slugs.add(slug)
                new_count += 1
                yield {
                    "_slug": slug,
                    "_href": href,
                    "_url":  urljoin(self.BASE_URL, href),
                    "_html": str(card),
                }
            if new_count == 0:
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        slug = payload.get("_slug") or ""
        url  = payload.get("_url") or ""
        if not slug or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # Title is the <h5> anchor text.
        title = ""
        h5 = soup.find("h5")
        if h5:
            title = _clean(h5.get_text(" ", strip=True))

        # Company sits in the la-building-o row; the icon sits inside a
        # <strong> wrapper alongside the company name. Strip the icon's
        # zero-width content before measuring.
        company = ""
        building_icon = soup.find("i", class_=re.compile(r"\bla-building-o\b"))
        if building_icon:
            strong = building_icon.find_parent("strong") or building_icon.parent
            company = _clean(strong.get_text(" ", strip=True)) if strong else ""

        # Location row (la-map-marker). May be missing on premium cards.
        location = _icon_text(soup, "la-map-marker") or None

        # Contract type (Full time / Part time / Contract). Not used in
        # the RawJob shape — we only record it indirectly via description
        # so the algo's keyword pass can pick it up.
        contract = _icon_text(soup, "la-briefcase") or ""

        # Posted-at: "Posted 9 hours ago" / "Posted 2 days ago". The site
        # does NOT expose an absolute date on the card, so we leave
        # posted_at=None and let BaseScraper.normalize fill in scrape time.
        # (Daily cadence makes "today" close enough for ranking.)
        posted_at = None

        if not title or not company:
            return None

        # Remote inference: location text mentions remote/worldwide.
        remote = None
        if location:
            lower = location.lower
            if "remote" in lower or "worldwide" in lower or "anywhere" in lower:
                remote = True
            elif "office" in lower or "onsite" in lower or "on-site" in lower:
                remote = False

        # Description: tiny one-liner with the contract type so the
        # scoring engine has SOMETHING beyond title + company. Keeps
        # the row useful in the feed even when we don't fetch detail.
        description = contract or None

        return RawJob(
            native_id=slug,
            title=title,
            company=company,
            url=url,
            location=location,
            description=description,
            posted_at=posted_at,
            remote=remote,
            raw=payload,
        )
