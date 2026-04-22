"""GamesIndustry.biz scraper — HTML.

GamesIndustry.biz Jobs (https://jobs.gamesindustry.biz) is one of the
oldest and most-trusted gaming-industry job boards. Heavy on UK/EU but
includes US studios. Strong for senior/exec roles in publishing,
production, business affairs.

Listing URL: https://jobs.gamesindustry.biz/jobs
Each job card is a structured DOM with:
  h2.title  → role
  .company  → employer
  .location → location
  a[href]   → detail page link

Same defensive pattern as bettingjobs.py / hitmarker.py — we don't N+1
into detail pages on every run; the listing card has enough for scoring.
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


def _clean(s: str) -> str:
    if not s:
        return ""
    return _WHITESPACE_RE.sub(" ", s).strip


def _text_of(soup: BeautifulSoup, *,
             class_pattern: Optional[str] = None,
             itemprop: Optional[str] = None) -> str:
    """Find by class regex or itemprop, return cleaned text or ''."""
    node = None
    if class_pattern:
        node = soup.find(class_=re.compile(class_pattern, re.I))
    if node is None and itemprop:
        node = soup.find(attrs={"itemprop": itemprop})
    if node is None:
        return ""
    return _clean(node.get_text(" ", strip=True))


@register("gamesindustry")
class GamesIndustryScraper(BaseScraper):
    source_name = "gamesindustry"
    schedule = "cron(30 6 * * ? *)"
    rate_limit_rps = 0.5

    BASE_URL = "https://jobs.gamesindustry.biz"
    LIST_URL = "https://jobs.gamesindustry.biz/jobs"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    DEFAULT_MAX_PAGES = 3

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        max_pages = int(cfg.get("max_pages") or self.DEFAULT_MAX_PAGES)

        seen_hrefs: set[str] = set
        for page in range(1, max_pages + 1):
            # GI uses ?page=N for pagination.
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
            # Detail-page anchors look like /job/<slug>-<id> (singular).
            # The plural /jobs/<slug> is for company filter pages — skip them.
            # Live HTML emits ABSOLUTE URLs (https://jobs.gamesindustry.biz/job/...)
            # so we use a regex that matches both relative and absolute shapes.
            _job_href_re = re.compile(
                r"^(?:https?://(?:jobs\.)?gamesindustry\.biz)?/job/[^/?#]+/?$"
            )
            anchors = [
                a for a in soup.find_all("a", href=True)
                if _job_href_re.match(a["href"])
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
                # The card root is <article class="node--job-per-template ...">.
                # The anchor itself appears TWICE inside it (logo + h2 title) —
                # both anchors are wrapped by the same article. We want the full
                # article so parse can see the h2 title, the company span,
                # and the location div. anchor.parent is just <div class="job__logo">
                # which holds only the logo (not enough).
                article = anchor.find_parent(
                    "article",
                    class_=re.compile(r"node--job-per-template"),
                )
                yield {
                    "_href": href,
                    "_url":  urljoin(self.BASE_URL, href),
                    "_html": str(article or anchor.parent or anchor),
                }
            if new_count == 0:
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        href = payload.get("_href") or ""
        url  = payload.get("_url") or ""
        if not href or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # ---- title -------------------------------------------------------
        # Preferred: <h2 class="node__title"> contains an <a> with the role.
        # Fallback 1: any heading text.
        # Fallback 2: <a class="recruiter-job-link" title="..."> attribute.
        title = ""
        title_node = soup.find(["h2", "h3", "h4"])
        if title_node:
            title = _clean(title_node.get_text(" ", strip=True))
        if not title:
            link = soup.find("a", class_=re.compile(r"recruiter-job-link"))
            if link and link.get("title"):
                title = _clean(link["title"])

        # ---- company -----------------------------------------------------
        # Preferred: <span class="recruiter-company-profile-job-organization">.
        # Fallback 1: schema.org itemprop.
        # Fallback 2: <picture title="..."> in the logo block (the company
        #             name is duplicated there as the logo's title attribute).
        # Fallback 3: <img alt="..."> in the logo block.
        company = (_text_of(soup, class_pattern=r"company") or
                   _text_of(soup, itemprop="hiringOrganization"))
        if not company:
            pic = soup.find("picture")
            if pic and pic.get("title"):
                company = _clean(pic["title"])
        if not company:
            img = soup.find("img", alt=True)
            if img:
                company = _clean(img.get("title") or img.get("alt") or "")

        # ---- location ----------------------------------------------------
        # GI uses <div class="location"><span>City, Country</span></div>.
        location = _text_of(soup, class_pattern=r"location") or None

        # ---- posted_at ---------------------------------------------------
        # Optional: <span class="date">15 Apr 2026,</span>. Best-effort —
        # if the format ever changes we just leave posted_at=None.
        posted_at = None
        date_node = soup.find(class_=re.compile(r"\bdate\b"))
        if date_node:
            from datetime import datetime
            raw_date = _clean(date_node.get_text(" ", strip=True)).rstrip(",")
            for fmt in ("%d %b %Y", "%d %B %Y"):
                try:
                    posted_at = datetime.strptime(raw_date, fmt).isoformat
                    break
                except ValueError:
                    continue

        if not title or not company:
            return None

        native_id = href.rstrip("/").rsplit("/", 1)[-1] or ""
        if not native_id:
            return None

        remote = None
        if location:
            lower = location.lower
            if "remote" in lower:
                remote = True
            elif "onsite" in lower or "on-site" in lower or "office" in lower:
                remote = False

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=url,
            location=location,
            description=None,
            posted_at=posted_at,
            remote=remote,
            raw=payload,
        )
