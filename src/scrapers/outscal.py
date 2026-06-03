"""Outscal Jobs scraper — HTML.

Outscal (https://outscal.com) is a games-industry careers platform with
a sizeable job board. Skews toward dev/eng/design but has senior product
and management roles too. Strong India + EU studio coverage with US too.

DOM shape (Next.js + Tailwind / shadcn-ui template, locked-in 2026-04):
    <a class="block" href="/job/<slug>">
      <div class="border ...">
        <div class="p-2">
          <div class="flex items-start gap-4">
            <img alt="<JobTitle>" src=".../<company>.png" />   <!-- alt is the JOB TITLE not company -->
            <div class="flex-1 min-w-0">
              <h3 class="font-semibold ...">Job Title</h3>
              <p class="text-muted-foreground text-sm font-medium">Company Name</p>
              <p class="text-muted-foreground text-sm mb-2 line-clamp-1">City, Country (Hybrid)</p>
              <div>...skill chips...</div>
            </div>
          </div>
        </div>
      </div>
    </a>

Note: the <img alt> on Outscal is the role title (not the company), so
we MUST NOT use it as a company fallback — that would scramble the data.
The company always lives in the first .font-medium <p>.
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


@register("outscal")
class OutscalScraper(BaseScraper):
    source_name = "outscal"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily
    rate_limit_rps = 1.0

    BASE_URL = "https://outscal.com"
    LIST_URL = "https://outscal.com/jobs"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        # Single GET — Next.js server-renders the first page of jobs into
        # the initial HTML; no pagination needed for daily-cadence sourcing.
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

        # Job cards: <a class="block" href="/job/<slug>">. The "block" class
        # on a Tailwind site is generic, so we anchor on the href shape
        # (must start with "/job/") to filter out nav/UI links.
        seen_hrefs: set[str] = set
        for anchor in soup.find_all("a", href=True):
            href = anchor["href"]
            if not href.startswith("/job/") or href == "/job/":
                continue
            if href in seen_hrefs:
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
        # Preferred: <h3 class="font-semibold ..."> — Outscal's canonical
        # title slot. Fallback: the img's alt attribute (Outscal puts the
        # role title in alt, not the company name).
        title = ""
        title_node = soup.find("h3")
        if title_node:
            title = _clean(title_node.get_text(" ", strip=True))
        if not title:
            img = soup.find("img", alt=True)
            if img:
                title = _clean(img["alt"])

        # ---- company -----------------------------------------------------
        # First <p class="font-medium"> in the card body.
        # IMPORTANT: do NOT fall back to img.alt — Outscal's alt is the
        # role title, not the company. Better to skip the row than scramble.
        company = ""
        for p in soup.find_all("p"):
            classes = " ".join(p.get("class") or )
            if "font-medium" in classes:
                txt = _clean(p.get_text(" ", strip=True))
                if txt:
                    company = txt
                    break

        # ---- location ----------------------------------------------------
        # Second <p> with class containing "line-clamp-1". On Outscal this
        # is the city/country line, often suffixed with "(Hybrid)" or
        # "(Remote)" or "(Onsite)".
        location = None
        for p in soup.find_all("p"):
            classes = " ".join(p.get("class") or )
            if "line-clamp-1" in classes and "font-medium" not in classes:
                txt = _clean(p.get_text(" ", strip=True))
                if txt:
                    location = txt
                    break

        if not title or not company:
            return None

        # native_id = slug after /job/.
        native_id = href.rstrip("/").rsplit("/", 1)[-1] or ""
        if not native_id:
            return None

        # remote inference from the location string suffix.
        remote = None
        if location:
            lower = location.lower
            if "remote" in lower or "anywhere" in lower:
                remote = True
            elif "onsite" in lower or "on-site" in lower or "in-office" in lower:
                remote = False
            # "(Hybrid)" stays None per existing scraper convention.

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,
            posted_at=None,
            remote=remote,
            raw=payload,
        )
