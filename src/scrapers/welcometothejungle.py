"""Welcome to the Jungle scraper — HTML.

Welcome to the Jungle (https://www.welcometothejungle.com) is a
French-origin tech jobs board with US, EU, and remote coverage. Mostly
mid-market and growth-stage tech companies. Useful for surfacing
fast-growing companies a curated company list might miss.

Listing URL: https://www.welcometothejungle.com/en/jobs
WTTJ uses a heavy React SPA — server-side HTML still includes structured
job cards as Next.js hydration data. We parse the static markup; if the
site goes pure-CSR in the future, this will silently produce 0 jobs and
we'll see it on health.html.

Card structure (best-effort):
  a[href*="/companies/<co>/jobs/<role>"]
  h4 → role title
  span/p with company-name (also embedded in the URL slug)
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
# WTTJ URL pattern: /en/companies/<company-slug>/jobs/<job-slug>
_JOB_URL_RE = re.compile(r"/(?:en/)?companies/([^/]+)/jobs/([^/?]+)")


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


@register("welcometothejungle")
class WelcomeToTheJungleScraper(BaseScraper):
    source_name = "welcometothejungle"
    schedule = "cron(30 6 * * ? *)"
    rate_limit_rps = 0.5

    BASE_URL = "https://www.welcometothejungle.com"
    LIST_URL = "https://www.welcometothejungle.com/en/jobs"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    DEFAULT_MAX_PAGES = 2

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        max_pages = int(cfg.get("max_pages") or self.DEFAULT_MAX_PAGES)

        seen_hrefs: set[str] = set
        for page in range(1, max_pages + 1):
            url = self.LIST_URL if page == 1 else f"{self.LIST_URL}?page={page}"
            self._throttle
            try:
                resp = requests.get(
                    url,
                    headers={
                        "User-Agent": self.USER_AGENT,
                        "Accept": "text/html",
                    },
                    timeout=30,
                )
                resp.raise_for_status
            except requests.RequestException:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            anchors = [
                a for a in soup.find_all("a", href=True)
                if _JOB_URL_RE.search(a["href"])
            ]
            if not anchors:
                break

            new_count = 0
            for anchor in anchors:
                href = anchor["href"]
                if href in seen_hrefs:
                    continue
                seen_hrefs.add(href)
                new_count += 1
                yield {
                    "_href": href,
                    "_url":  urljoin(self.BASE_URL, href),
                    "_html": str(anchor.parent or anchor),
                }
            if new_count == 0:
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        href = payload.get("_href") or ""
        url  = payload.get("_url") or ""
        if not href or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # Title: any heading inside the card.
        title_node = soup.find(["h2", "h3", "h4", "h5"])
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""

        # Company: extract from URL slug as a reliable fallback. WTTJ URL
        # convention is /companies/<company-slug>/jobs/<job-slug> and the
        # slug is dash-separated lowercase. Prettify by title-casing.
        m = _JOB_URL_RE.search(href)
        company_slug = m.group(1) if m else ""
        job_slug = m.group(2) if m else ""

        company = ""
        # Try DOM company hint first.
        for tag in soup.find_all(["span", "p", "div"]):
            if tag.find(["span", "p", "div", "h2", "h3", "h4", "h5"]):
                continue
            text = _clean(tag.get_text(" ", strip=True))
            if not text or text == title or len(text) > 80:
                continue
            # Skip location-shaped strings.
            if "remote" in text.lower or re.search(r",\s*[A-Z]{2}\b", text):
                continue
            company = text
            break

        # Fall back to URL slug if no DOM hint found.
        if not company and company_slug:
            company = company_slug.replace("-", " ").title

        if not title or not company:
            return None

        # Location: anything that looks city-shaped or remote.
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

        native_id = job_slug or href.rstrip("/").rsplit("/", 1)[-1]
        if not native_id:
            return None

        remote = None
        if location and "remote" in location.lower:
            remote = True

        return RawJob(
            native_id=f"{company_slug}:{native_id}" if company_slug else native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,
            posted_at=None,
            remote=remote,
            raw=payload,
        )
