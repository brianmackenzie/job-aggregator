"""Work With Indies scraper — HTML.

Work With Indies (https://workwithindies.com) is a curated job board
for indie game studios. Smaller volume than the big aggregators but
high signal — every listing is a real indie team, no noise from
recruiter spam or aggregator-of-aggregators.

DOM shape (Webflow CMS template, locked-in 2026-04):
    <div class="w-dyn-item">
      <a class="job-card" href="/careers/<slug>">
        <img alt="<Company Name>" class="company-logo">
        <div class="job-link-desktop">
          <div class="job-card-text bold">Company Name</div>
          <div class="text-block-28">Role Title</div>
          ...
          <div class="job-card-text bold">Location</div>   <!-- last bold = location -->
        </div>
      </a>
    </div>

We use the structured selectors above. If Webflow changes the class
suffixes (-28, -14, etc.) the parse falls back to "first bold = company,
text-block-28 = title, last bold = location" by selector order.

The site is single-page (no pagination) — Webflow CMS dumps all jobs
into the initial HTML and uses client-side JetBoost JS for filtering.
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


@register("work_with_indies")
class WorkWithIndiesScraper(BaseScraper):
    source_name = "work_with_indies"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily, with HTML batch
    rate_limit_rps = 1.0

    BASE_URL  = "https://workwithindies.com"
    LIST_URL  = "https://workwithindies.com/"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        # Single GET — site is single-page, no pagination.
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

        # Job cards are <a class="job-card"> with href="/careers/<slug>".
        # Each anchor sits inside a Webflow .w-dyn-item wrapper. We yield
        # the anchor's outerHTML — it contains the company-logo img, the
        # title, and the location all inside the .job-link-desktop div.
        seen_hrefs: set[str] = set
        for anchor in soup.find_all("a", class_=re.compile(r"\bjob-card\b"),
                                    href=True):
            href = anchor["href"]
            if not href.startswith("/careers/") or href in seen_hrefs:
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
        # Preferred: <div class="text-block-28"> holds just the role title.
        # Fallback: <div class="text-block-14"> in the mobile block.
        title = ""
        title_node = soup.find(class_="text-block-28")
        if title_node is None:
            title_node = soup.find(class_="text-block-14")
        if title_node:
            title = _clean(title_node.get_text(" ", strip=True))

        # ---- company -----------------------------------------------------
        # Preferred: <img class="company-logo" alt="Company Name">.
        # Fallback: first .job-card-text.bold div (the desktop block puts
        # company in bold, then a static "is hiring a", then the role).
        company = ""
        img = soup.find("img", class_=re.compile(r"company-logo"))
        if img and img.get("alt"):
            company = _clean(img["alt"])
        if not company:
            bold = soup.find(class_=re.compile(r"job-card-text"))
            if bold:
                company = _clean(bold.get_text(" ", strip=True))

        # ---- location ----------------------------------------------------
        # The desktop block ends with another .job-card-text.bold div whose
        # text is the location ("Anywhere", "United States", "London", etc.).
        # We collect ALL .bold text-blocks and take the LAST one — the first
        # is company. This matches both desktop and mobile templates.
        location = None
        bolds = soup.find_all(class_=re.compile(r"\bbold\b"))
        if len(bolds) >= 2:
            location = _clean(bolds[-1].get_text(" ", strip=True))

        if not title or not company:
            # Selectors didn't find the canonical shape — skip rather than
            # fabricate a half-broken row.
            return None

        # native_id = slug after /careers/. Webflow guarantees uniqueness
        # per company+role combo so this is stable across runs.
        native_id = href.rstrip("/").rsplit("/", 1)[-1] or ""
        if not native_id:
            return None

        # remote inference. Indies use "Anywhere" or "Remote" liberally.
        remote = None
        if location:
            lower = location.lower
            if "anywhere" in lower or "remote" in lower or "worldwide" in lower:
                remote = True
            elif "onsite" in lower or "on-site" in lower or "office" in lower:
                remote = False

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,           # Card has no JD body
            posted_at=None,             # No date on listing card
            remote=remote,
            raw=payload,
        )
