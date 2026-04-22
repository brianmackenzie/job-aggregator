"""Fractional Jobs scraper — HTML.

Fractional Jobs (https://www.fractionaljobs.io) curates fractional /
part-time executive roles. Especially interesting as a "land
the next role" alternative path: fractional CTO/Head-of-Eng/VP-Product
for early-stage startups while a full-time search is in motion.

The site doesn't expose a feed/API, so we scrape the public listing page.
The DOM has been stable: each job card is an <a> with class containing
"job_card" or similar, wrapping inner divs for title / company / location.

Defensive parsing strategy:
  * Find every <a> whose href starts with "/jobs/" — those are job links.
  * Inside each, pick the first heading-ish element (h1/h2/h3) as title.
  * The company name is usually the second visible text node, often in a
    <p> with class containing "company".
  * Location/remote is a third short text snippet.

If selectors stop matching, parse returns None per row and the source
silently produces 0 jobs that day. The base scraper logs that as a
"partial" run and we'll see it on health.html.
"""
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


_WHITESPACE_RE = re.compile(r"\s+")


def _clean(s: str) -> str:
    """Normalize whitespace; bs4 leaves runs of \\n and tabs in get_text."""
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


@register("fractional_jobs")
class FractionalJobsScraper(BaseScraper):
    source_name = "fractional_jobs"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily
    rate_limit_rps = 1.0

    BASE_URL  = "https://www.fractionaljobs.io"
    # Note: as of 2026-04 the homepage IS the jobs listing (single-page site).
    # /jobs returns 404. If they ever add a dedicated index, swap this back.
    LIST_URL  = "https://www.fractionaljobs.io/"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        # Single GET — listing page is a flat list, no pagination today.
        # If they add pagination later we'll add a loop here.
        self._throttle
        resp = requests.get(
            self.LIST_URL,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "text/html",
            },
            timeout=30,
        )
        resp.raise_for_status

        soup = BeautifulSoup(resp.text, "html.parser")

        # Job cards are <a> tags with hrefs starting "/jobs/" — but the
        # anchor itself is EMPTY on Fractional Jobs (Webflow site pattern).
        # The actual card content lives in a sibling/ancestor div with
        # class "job-item". Walk up to that ancestor for parse to use.
        seen_hrefs: set[str] = set
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not href.startswith("/jobs/") or href == "/jobs":
                continue
            if href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            card = anchor.find_parent("div", class_=re.compile(r"\bjob-item\b"))
            yield {
                "_href": href,
                "_url":  urljoin(self.BASE_URL, href),
                "_html": str(card or anchor),
            }

    def parse(self, payload: dict) -> Optional[RawJob]:
        href = payload.get("_href") or ""
        url  = payload.get("_url") or ""
        if not href or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # Fractional Jobs Webflow template: the title block has the shape
        #   <div class="job-item_name_url">
        #     <div ...>
        #       <h3>Company Name</h3>
        #       <h3> - </h3>
        #       <h3>Role Title</h3>
        #     </div>
        #   </div>
        # We collect ALL h3 text inside .job-item_name_url, join with space,
        # then split on " - " (the literal separator the template injects).
        company = ""
        title   = ""
        location = None

        name_url = soup.find("div", class_=re.compile(r"job-item_name_url"))
        if name_url:
            h3_texts = [_clean(h.get_text(" ", strip=True))
                        for h in name_url.find_all("h3")]
            joined = _clean(" ".join(t for t in h3_texts if t))
            # joined is e.g. "A Small Business Acquisition Marketplace - Senior Full-stack Engineer"
            if " - " in joined:
                left, right = joined.split(" - ", 1)
                company = _clean(left)
                title   = _clean(right)
            else:
                # No separator — assume the whole string is the role title.
                title = joined

        # Location / hours / pay: in <div class="job-item_more-info">
        # joined like "20 - 40 hrs | $125 - $150 / hr | Hybrid (NYC only)".
        # Take the last pipe-segment as the location signal.
        more_info = soup.find("div", class_=re.compile(r"job-item_more-info"))
        if more_info:
            text = _clean(more_info.get_text(" | ", strip=True))
            # Split on " | " — last token is typically the location.
            parts = [p.strip for p in re.split(r"\|", text) if p.strip]
            if parts:
                location = parts[-1]

        # Fallback to old leaf-tag heuristic if structured selectors miss
        # (e.g. fixture HTML in tests, or template change in the wild).
        if not title:
            heading = soup.find(["h1", "h2", "h3"])
            if heading:
                title = _clean(heading.get_text(" ", strip=True))

        if not title or not company:
            for tag in soup.find_all(["p", "span", "div"]):
                if tag.find(["p", "span", "div", "h1", "h2", "h3"]):
                    continue
                text = _clean(tag.get_text(" ", strip=True))
                if not text or text == title or len(text) > 80:
                    continue
                looks_like_location = (
                    "remote" in text.lower
                    or "anywhere" in text.lower
                    or re.search(r",\s*[A-Z]{2}\b", text)
                )
                if looks_like_location and not location:
                    location = text
                    continue
                if not company:
                    company = text
                    continue

        if not title or not company:
            # Selectors didn't find a clean shape — skip rather than fabricate.
            return None

        # native_id = slug after /jobs/. URLs look like /jobs/<slug>.
        native_id = href.rstrip("/").rsplit("/", 1)[-1]
        if not native_id:
            return None

        # remote inference from the location string.
        remote = None
        if location:
            lower = location.lower
            if "remote" in lower or "anywhere" in lower:
                remote = True
            elif "onsite" in lower or "on-site" in lower:
                remote = False

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,            # Listing page doesn't include the JD body
            posted_at=None,              # No date on the listing card
            remote=remote,
            raw=payload,
        )
