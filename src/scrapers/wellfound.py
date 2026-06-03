"""Wellfound (fka AngelList Talent) scraper — HTML.

Wellfound (https://wellfound.com) hosts startup roles, mostly Series-A
through Series-D. High signal for "VP/Head of Eng/Product at growth
startup" plays in the user's career-pivot lane.

Wellfound has historically been hostile to scrapers (they require an
authenticated session for the rich job-listing UI). The PUBLIC role
search URL still serves a partially-hydrated SSR page with a job count
and per-company role cards — we scrape what's visible to anonymous
visitors and accept the lower yield.

If Wellfound moves the public listing entirely behind login, this scraper
will silently produce 0 jobs; the per-source try/except in BaseScraper
ensures it doesn't poison the rest of the daily run.

Listing URL: https://wellfound.com/role/l/<role>/<location>
We hit a small set of pre-canned URL patterns from sources.yaml.
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
# Wellfound URL shapes to detect: /jobs/<id>-<slug>, /company/<co>/jobs/<id>-<slug>
_JOB_HREF_RE = re.compile(r"/(?:company/[^/]+/)?jobs/(\d+)(?:-([^/?]+))?")


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


@register("wellfound")
class WellfoundScraper(BaseScraper):
    source_name = "wellfound"
    schedule = "cron(30 6 * * ? *)"
    rate_limit_rps = 0.5

    BASE_URL = "https://wellfound.com"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    # Generic placeholder defaults — override via config/sources.yaml ->
    # wellfound.search_urls with the role/location slugs that match your
    # search. Wellfound URL shape is /role/l/<role-slug>/<location-slug>;
    # browse https://wellfound.com to copy the slugs you want.
    DEFAULT_SEARCH_URLS = [
        "https://wellfound.com/role/l/software-engineer/remote",
    ]

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        search_urls = cfg.get("search_urls") or self.DEFAULT_SEARCH_URLS

        seen_hrefs: set[str] = set
        for search_url in search_urls:
            self._throttle
            try:
                resp = requests.get(
                    search_url,
                    headers={
                        "User-Agent": self.USER_AGENT,
                        "Accept": "text/html",
                    },
                    timeout=30,
                )
                resp.raise_for_status
            except requests.RequestException:
                # Wellfound 403s a lot — log via the per-item path and continue.
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            for anchor in soup.find_all("a", href=True):
                href = anchor["href"]
                if not _JOB_HREF_RE.search(href):
                    continue
                if href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                yield {
                    "_href": href,
                    "_url":  urljoin(self.BASE_URL, href),
                    "_html": str(anchor.parent or anchor),
                    "_search_url": search_url,
                }

    def parse(self, payload: dict) -> Optional[RawJob]:
        href = payload.get("_href") or ""
        url  = payload.get("_url") or ""
        if not href or not url:
            return None

        m = _JOB_HREF_RE.search(href)
        if not m:
            return None
        job_id = m.group(1)               # numeric — globally unique on WF
        # job_slug = m.group(2)           # not currently used for native_id

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        title_node = soup.find(["h2", "h3", "h4", "h5"])
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""

        # Company: try to extract from URL pattern /company/<co>/jobs/...
        # Else use the first short DOM text node before the title.
        company = ""
        co_match = re.search(r"/company/([^/]+)/", href)
        if co_match:
            company = co_match.group(1).replace("-", " ").title

        if not company:
            for tag in soup.find_all(["span", "p", "div"]):
                if tag.find(["span", "p", "div", "h2", "h3", "h4", "h5"]):
                    continue
                text = _clean(tag.get_text(" ", strip=True))
                if not text or text == title or len(text) > 80:
                    continue
                if "remote" in text.lower or re.search(r",\s*[A-Z]{2}\b", text):
                    continue
                company = text
                break

        # Location: separately scan for a remote/city-shaped node.
        location = None
        for tag in soup.find_all(["span", "p", "div"]):
            if tag.find(["span", "p", "div", "h2", "h3", "h4", "h5"]):
                continue
            text = _clean(tag.get_text(" ", strip=True))
            if not text or text == title or text == company or len(text) > 80:
                continue
            if "remote" in text.lower or re.search(r",\s*[A-Z]{2}\b", text):
                location = text
                break

        if not title or not company:
            return None

        remote = None
        if location and "remote" in location.lower:
            remote = True

        return RawJob(
            native_id=job_id,                 # WF numeric job id is unique
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,
            posted_at=None,
            remote=remote,
            raw=payload,
        )
