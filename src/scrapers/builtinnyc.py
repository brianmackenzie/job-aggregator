"""Built In NYC scraper — HTML.

Built In NYC (https://www.builtinnyc.com) is a NYC-focused tech jobs
board with a strong gaming/media slice. Geo-pinned to NYC = high
fit for the user's NYC-metro track even on non-gaming companies.

Listing URL: https://www.builtinnyc.com/jobs
Each card has:
  h2.title  → role
  .company-name (or anchor)
  .location-text → location, often "New York, NY (Remote)" / "Hybrid"
  Detail href: /job/<slug>

We aggregate across pagination and rely on the listing card for
title + company + location. The detail page contains a salary block
in some listings; we don't fetch it (would be N+1 HTTP calls).
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
    node = None
    if class_pattern:
        node = soup.find(class_=re.compile(class_pattern, re.I))
    if node is None and itemprop:
        node = soup.find(attrs={"itemprop": itemprop})
    if node is None:
        return ""
    return _clean(node.get_text(" ", strip=True))


@register("builtinnyc")
class BuiltInNYCScraper(BaseScraper):
    source_name = "builtinnyc"
    schedule = "cron(30 6 * * ? *)"
    rate_limit_rps = 0.5

    BASE_URL = "https://www.builtinnyc.com"
    LIST_URL = "https://www.builtinnyc.com/jobs"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    DEFAULT_MAX_PAGES = 3

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        max_pages = int(cfg.get("max_pages") or self.DEFAULT_MAX_PAGES)

        seen_hrefs: set[str] = set
        for page in range(1, max_pages + 1):
            # BuiltIn uses /jobs?page=N for pagination.
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
                if a["href"].startswith("/job/")
                and a["href"] != "/job/"
                and "?" not in a["href"]
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
                # The full card root is <div data-id="job-card"> — it holds
                # the company logo (<img data-id="company-img">), the company
                # link (<a data-id="company-title">), the title h2 (with
                # <a data-id="job-card-title">), and the work mode + location
                # spans. anchor.parent is just the inner h2 — not enough.
                card = anchor.find_parent("div", attrs={"data-id": "job-card"})
                yield {
                    "_href": href,
                    "_url":  urljoin(self.BASE_URL, href),
                    "_html": str(card or anchor.parent or anchor),
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
        # Preferred: <a data-id="job-card-title"> — the live BuiltIn shape.
        # Fallback: any heading text.
        title = ""
        title_anchor = soup.find(attrs={"data-id": "job-card-title"})
        if title_anchor:
            title = _clean(title_anchor.get_text(" ", strip=True))
        if not title:
            title_node = soup.find(["h2", "h3", "h4"])
            if title_node:
                title = _clean(title_node.get_text(" ", strip=True))

        # ---- company -----------------------------------------------------
        # Preferred: <a data-id="company-title"><span>Company Name</span></a>.
        # Fallback 1: <img data-id="company-img" alt="Company Name Logo"> —
        #             strip the trailing " Logo" suffix the BuiltIn template
        #             always appends.
        # Fallback 2: legacy class= selector / itemprop.
        company = ""
        company_anchor = soup.find(attrs={"data-id": "company-title"})
        if company_anchor:
            company = _clean(company_anchor.get_text(" ", strip=True))
        if not company:
            company_img = soup.find("img", attrs={"data-id": "company-img"})
            if company_img and company_img.get("alt"):
                company = _clean(company_img["alt"])
                # Strip BuiltIn's "<Name> Logo" alt-text suffix.
                company = re.sub(r"\s+logo$", "", company, flags=re.I)
        if not company:
            company = (_text_of(soup, class_pattern=r"company") or
                       _text_of(soup, itemprop="hiringOrganization"))

        # ---- location / work mode ---------------------------------------
        # The card has two text spans inside .bounded-attribute-section:
        # one for work mode (In-Office / Hybrid / Remote) and one for
        # the city. Each is preceded by a <i> icon (fa-house-building for
        # work mode, fa-location-dot for city). We collect both and join
        # so the score engine sees e.g. "In-Office, New York, NY".
        location_parts: list[str] = 
        attr_section = soup.find(class_=re.compile(r"bounded-attribute-section"))
        if attr_section:
            for icon in attr_section.find_all("i", class_=re.compile(
                    r"fa-(house-building|location-dot)")):
                # The text span sits as the next-sibling element of the icon's
                # wrapper div — easier to walk up to a common parent and grab
                # the first text-bearing span.
                wrapper = icon.find_parent("div")
                if wrapper:
                    sibling = wrapper.find_next_sibling
                    if sibling:
                        text = _clean(sibling.get_text(" ", strip=True))
                        if text:
                            location_parts.append(text)
        if not location_parts:
            # Legacy fallback for fixture HTML in tests.
            legacy = (_text_of(soup, class_pattern=r"location") or
                      _text_of(soup, itemprop="jobLocation"))
            if legacy:
                location_parts.append(legacy)
        location = ", ".join(location_parts) if location_parts else None

        if not title or not company:
            return None

        native_id = href.rstrip("/").rsplit("/", 1)[-1] or ""
        if not native_id:
            return None

        # remote inference. BuiltIn uses "Remote", "Hybrid", "In-Office".
        remote = None
        if location:
            lower = location.lower
            if "remote" in lower:
                remote = True
            elif "in-office" in lower or "in office" in lower or "onsite" in lower:
                remote = False
            # "Hybrid" stays None (per the same convention as Apify scraper).

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
