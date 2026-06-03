"""Remote Game Jobs scraper — HTML.

Remote Game Jobs (https://remotegamejobs.com) is a 100%-remote-only
gaming-industry job board. The intersection of "gaming" + "remote" is
exactly an NYC-base + remote-friendly track, so this is one of
the highest signal-per-listing sources in the batch.

DOM shape (Bulma CSS template, locked-in 2026-04):
    <div class="job-box box has-background-light hvr-grow-shadow">
      <a class="has-text-black"
         href="https://remotegamejobs.com/jobs/<slug>"
         title="<Company> is hiring <Role> (Remote Job)">
        <article class="media">
          <figure class="image is-64x64">
            <img src=".../<company>-logo.png">
          </figure>
          <div class="media-content">
            <strong class="f-20">Role Title</strong>
            <small class="f-15">Company Name</small>
            <span><i class="fas fa-hourglass"></i> Full-Time</span>
            <span class="tag is-warning">tag1</span>
            <span class="tag is-warning">tag2</span>
            ...
          </div>
        </article>
      </a>
    </div>

The card is delightfully clean: title in <strong class="f-20">,
company in <small class="f-15">, employment type in the <i class="fa-hourglass">
sibling, tags as <span class="tag is-warning"> chips.

Every job here is remote by definition (it's the entire site's premise),
so we hard-set remote=True.
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


@register("remote_game_jobs")
class RemoteGameJobsScraper(BaseScraper):
    source_name = "remote_game_jobs"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily
    rate_limit_rps = 1.0

    BASE_URL = "https://remotegamejobs.com"
    LIST_URL = "https://remotegamejobs.com/"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        # Single GET — homepage is the listing, all jobs server-rendered.
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

        # Each card is <div class="job-box"> with a single <a> wrapping
        # an <article class="media">. We yield the .job-box outerHTML so
        # parse has the full card available (logo + title + company + tags).
        seen_hrefs: set[str] = set
        for box in soup.find_all("div", class_=re.compile(r"\bjob-box\b")):
            anchor = box.find("a", href=True)
            if anchor is None:
                continue
            href = anchor["href"]
            # Job detail URLs: https://remotegamejobs.com/jobs/<slug>
            if "/jobs/" not in href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)
            yield {
                "_href": href,
                "_url":  urljoin(self.BASE_URL, href),
                "_html": str(box),
            }

    def parse(self, payload: dict) -> Optional[RawJob]:
        href = payload.get("_href") or ""
        url  = payload.get("_url") or ""
        if not href or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # ---- title -------------------------------------------------------
        # Preferred: <strong class="f-20"> holds the role title.
        # Fallback: anchor's title attribute is shaped "<Company> is hiring
        # <Role> (Remote Job)" — extract the middle.
        title = ""
        title_node = soup.find("strong", class_=re.compile(r"f-20"))
        if title_node:
            title = _clean(title_node.get_text(" ", strip=True))
        if not title:
            anchor = soup.find("a", attrs={"title": True})
            if anchor:
                t = anchor["title"]
                # "Foo Studio is hiring Bar Engineer (Remote Job)"
                m = re.match(
                    r"^.+?\s+is hiring\s+(.+?)\s*\(Remote Job\)\s*$", t, re.I)
                if m:
                    title = _clean(m.group(1))
                else:
                    title = _clean(t)

        # ---- company -----------------------------------------------------
        # Preferred: <small class="f-15"> directly under the title.
        # Fallback: leading clause of the anchor title attribute.
        company = ""
        small = soup.find("small", class_=re.compile(r"f-15"))
        if small:
            company = _clean(small.get_text(" ", strip=True))
        if not company:
            anchor = soup.find("a", attrs={"title": True})
            if anchor:
                t = anchor["title"]
                m = re.match(r"^(.+?)\s+is hiring\s+", t, re.I)
                if m:
                    company = _clean(m.group(1))

        # ---- tags / location --------------------------------------------
        # The site uses <span class="tag is-warning"> for skill tags. There's
        # no dedicated location field — every job is remote (site premise).
        # We collect the tag chips into the description for downstream scoring
        # (the keywords engine reads description for skill-keyword matches).
        tags = [
            _clean(t.get_text(" ", strip=True))
            for t in soup.find_all("span", class_=re.compile(r"\btag\b"))
        ]
        tags = [t for t in tags if t]
        description = ", ".join(tags) if tags else None

        if not title or not company:
            return None

        # native_id = slug after /jobs/. Site uses long slugs with UUID
        # suffixes — they're stable per posting.
        native_id = href.rstrip("/").rsplit("/", 1)[-1] or ""
        if not native_id:
            return None

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location="Remote",       # Site-wide premise, hard-coded.
            description=description,
            posted_at=None,          # No date on listing card
            remote=True,             # Site is 100% remote-only.
            raw=payload,
        )
