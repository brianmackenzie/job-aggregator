"""Games Jobs Direct scraper — HTML.

Games Jobs Direct (https://www.gamesjobsdirect.com) is a UK-headquartered
gaming-industry job board with strong UK + EU coverage and growing US
presence. Server-rendered HTML with stable, semantic class names — one
of the cleaner targets in the batch.

DOM shape (locked-in 2026-04):
    <a href="/job/<recruiter-slug>/<role-slug>/<id>">
      <h4 class="job-title">Job Title</h4>
      <p class="job-location">City, Country</p>
      <div class="job-desc margin-b-1">Description snippet (~100 chars)...</div>
      <div style="width: 120px">
        <div class="outer">
          <div class="inner"
               style="background-image:url(/assets/.../employer-logo-N.png)"
               title="Posted by <Company Name>">
          </div>
        </div>
      </div>
    </a>

Note: company name is NOT in clean text — it's stuffed into the
title attribute of the logo background div ("Posted by <Company>").
We strip the "Posted by " prefix to extract just the company name.

native_id is the trailing /<id> segment of the URL (e.g. "336631") —
guaranteed unique per posting by the site.
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
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


@register("games_jobs_direct")
class GamesJobsDirectScraper(BaseScraper):
    source_name = "games_jobs_direct"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily
    rate_limit_rps = 1.0

    BASE_URL = "https://www.gamesjobsdirect.com"
    LIST_URL = "https://www.gamesjobsdirect.com/"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    # /job/<recruiter>/<slug>/<id> — id is numeric.
    _job_href_re = re.compile(r"^/job/[^/]+/[^/]+/\d+/?$")

    def fetch(self) -> Iterable[dict]:
        # Single GET — homepage is the listing, all jobs server-rendered.
        # The site has /jobs subpaths for filters; the homepage already
        # surfaces the most-recent + featured set, which is enough for a
        # daily-cadence batch.
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

        seen_hrefs: set[str] = set
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not self._job_href_re.match(href) or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            yield {
                "_href": href,
                "_url":  urljoin(self.BASE_URL, href),
                "_html": str(anchor),
            }

    def parse(self, payload: dict) -> Optional[RawJob]:
        href = payload.get("_href") or ""
        url  = payload.get("_url") or ""
        if not href or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # ---- title -------------------------------------------------------
        # <h4 class="job-title"> is the canonical slot.
        title = ""
        title_node = soup.find("h4", class_=re.compile(r"job-title"))
        if title_node is None:
            title_node = soup.find(["h2", "h3", "h4"])
        if title_node:
            title = _clean(title_node.get_text(" ", strip=True))

        # ---- company -----------------------------------------------------
        # The site puts the company name in title="Posted by <Company>"
        # on the inner div of the logo. We strip the "Posted by " prefix.
        # Fallback: extract from the URL slug (the recruiter segment).
        company = ""
        logo_div = soup.find("div", attrs={"title": re.compile(
            r"^Posted by\s+", re.I)})
        if logo_div:
            raw_title = logo_div["title"]
            company = _clean(re.sub(r"^Posted by\s+", "", raw_title, flags=re.I))
        if not company:
            # Fallback: /job/<recruiter-slug>/<role-slug>/<id> — un-slugify
            # the first segment. Imperfect (loses casing/punctuation) but
            # better than dropping the row entirely.
            parts = href.strip("/").split("/")
            if len(parts) >= 4 and parts[0] == "job":
                company = _clean(parts[1].replace("-", " ").title)

        # ---- location ----------------------------------------------------
        location = None
        loc_node = soup.find("p", class_=re.compile(r"job-location"))
        if loc_node:
            location = _clean(loc_node.get_text(" ", strip=True)) or None

        # ---- description (short snippet) --------------------------------
        description = None
        desc_node = soup.find("div", class_=re.compile(r"job-desc"))
        if desc_node:
            description = _clean(desc_node.get_text(" ", strip=True)) or None

        if not title or not company:
            return None

        # native_id = numeric trailing segment.
        native_id = href.rstrip("/").rsplit("/", 1)[-1] or ""
        if not native_id:
            return None

        # remote inference. Site doesn't have a structured remote field;
        # check both location AND description for "remote" / "hybrid".
        remote = None
        haystack = " ".join(s for s in (location, description) if s).lower
        if "remote" in haystack or "anywhere" in haystack or "worldwide" in haystack:
            remote = True
        elif "onsite" in haystack or "on-site" in haystack:
            remote = False
        # "hybrid" stays None per scraper convention.

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=description,
            posted_at=None,
            remote=remote,
            raw=payload,
        )
