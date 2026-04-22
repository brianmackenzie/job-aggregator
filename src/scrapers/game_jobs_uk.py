"""game_jobs_uk scraper — gamejobsuk.com.

UK-headquartered gaming-industry job board. Daily-fresh listings,
Tailwind-styled cards, infinite-scroll pagination via a `/get_jobs?offset=N`
XHR endpoint that returns rendered HTML chunks.

DOM shape per card (locked-in 2026-04-18):
    <a class="job-card" data-job-id="2579" href="/jobs?job_id=2579">
      <span class="industry">Programming</span>
      <span class="…">Hybrid</span>             (mode: Remote / Hybrid / Onsite)
      <span class="…">Full-Time</span>          (employment type)
      <h3 class="…">Creative Assembly</h3>     (← company)
      <h4 class="…">Senior Build Engineer</h4> (← title)
      <span>Horsham - United Kingdom</span>     (location, no class)
      <span class="local-date">17.04.2026</span> (added date, DD.MM.YYYY)
    </a>

The site renders 15 cards in the initial HTML and loads more via:
    GET /get_jobs?offset=15
    Headers: X-Requested-With: XMLHttpRequest, Referer: https://www.gamejobsuk.com/

Without `X-Requested-With` the endpoint returns 406 (Mod_Security guard),
so the scraper sets it explicitly. Each chunk is HTML, not JSON — we
re-parse it through the same selector pipeline.

We default to 4 pages (60 jobs); the live site rarely has more
than ~200 active listings.
"""
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.sources_config import load_source_config


_WHITESPACE_RE = re.compile(r"\s+")
_DATE_RE       = re.compile(r"(\d{2})\.(\d{2})\.(\d{4})")
# Mode pills are styled by colour but the text is one of these literals.
_MODE_TOKENS   = ("Remote", "Hybrid", "Onsite")


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


@register("game_jobs_uk")
class GameJobsUKScraper(BaseScraper):
    source_name = "game_jobs_uk"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily
    rate_limit_rps = 1.0

    BASE_URL  = "https://www.gamejobsuk.com"
    LIST_URL  = "https://www.gamejobsuk.com/"
    XHR_URL   = "https://www.gamejobsuk.com/get_jobs"
    USER_AGENT = (
        # The site Mod_Security guards on UA + Accept; a real browser
        # signature passes through. We attribute on Referer instead.
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
    )
    DEFAULT_MAX_PAGES   = 4   # 4 * 15 = 60 jobs per run
    PAGE_SIZE           = 15  # site is hard-coded to this — don't change

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        max_pages = int(cfg.get("max_pages") or self.DEFAULT_MAX_PAGES)

        seen_ids: set[str] = set
        for page in range(0, max_pages):    # page 0 = initial HTML
            self._throttle
            try:
                if page == 0:
                    resp = requests.get(
                        self.LIST_URL,
                        headers={"User-Agent": self.USER_AGENT, "Accept": "text/html"},
                        timeout=30,
                    )
                else:
                    # XHR endpoint demands the X-Requested-With header
                    # (Mod_Security guards against direct hits).
                    resp = requests.get(
                        self.XHR_URL,
                        headers={
                            "User-Agent":      self.USER_AGENT,
                            "Accept":          "text/html, */*; q=0.01",
                            "Referer":         self.LIST_URL,
                            "X-Requested-With": "XMLHttpRequest",
                        },
                        params={"offset": page * self.PAGE_SIZE},
                        timeout=30,
                    )
                resp.raise_for_status
            except requests.RequestException:
                break

            soup = BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all("a", class_="job-card")
            if not cards:
                break   # past the end of the listing

            new_count = 0
            for card in cards:
                job_id = card.get("data-job-id")
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                new_count += 1
                yield {
                    "_job_id": str(job_id),
                    "_href":   card.get("href") or f"/jobs?job_id={job_id}",
                    "_html":   str(card),
                }
            if new_count == 0:
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        job_id = payload.get("_job_id") or ""
        href   = payload.get("_href") or ""
        if not job_id:
            return None
        url = urljoin(self.BASE_URL, href)

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # h3 → company, h4 → title (locked-in shape; not interchangeable).
        h3 = soup.find("h3")
        h4 = soup.find("h4")
        company = _clean(h3.get_text(" ", strip=True)) if h3 else ""
        title   = _clean(h4.get_text(" ", strip=True)) if h4 else ""

        # Location: the only un-classed span containing " - " or known
        # country tokens. We scan span text and pick the one that smells
        # like a location (contains " - " AND is < 60 chars).
        location = None
        for span in soup.find_all("span"):
            classes = span.get("class") or 
            if classes:
                continue   # all our location <span>s are class-less
            text = _clean(span.get_text(" ", strip=True))
            if not text or len(text) > 60:
                continue
            if " - " in text or "United Kingdom" in text or "Remote" in text:
                location = text
                break

        # Mode (Remote / Hybrid / Onsite) — pulled from the coloured pills.
        # We use it for the remote inference even when the location string
        # doesn't say "Remote" directly.
        mode = None
        for span in soup.find_all("span"):
            txt = _clean(span.get_text(" ", strip=True))
            if txt in _MODE_TOKENS:
                mode = txt
                break

        # Posted-at: span.local-date ("17.04.2026" → ISO 2026-04-17).
        posted_at = None
        date_node = soup.find(class_="local-date")
        if date_node:
            m = _DATE_RE.match(_clean(date_node.get_text(strip=True)))
            if m:
                dd, mm, yyyy = m.groups
                posted_at = f"{yyyy}-{mm}-{dd}T00:00:00Z"

        if not title or not company:
            return None

        # Remote inference combines the explicit mode pill and any
        # "remote" token in the location string.
        remote = None
        if mode == "Remote":
            remote = True
        elif mode == "Onsite":
            remote = False
        # Mode "Hybrid" → leave None (algo treats unknown as neutral).
        if remote is None and location and "remote" in location.lower:
            remote = True

        return RawJob(
            native_id=job_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,
            posted_at=posted_at,
            remote=remote,
            raw=payload,
        )
