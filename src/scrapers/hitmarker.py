"""Hitmarker scraper — HTML.

Hitmarker (https://hitmarker.net) is a UK-based gaming/esports jobs board
with strong international coverage. The DOM uses Tailwind-ish utility
classes that change frequently — but anchor hrefs to /jobs/{slug} are
stable, and each card has a recognizable h2/h3 + company-line structure.

We follow the same defensive pattern as bettingjobs.py:
  * Find <a> hrefs starting with /jobs/
  * Look for the title heading inside
  * Walk leaf text nodes for company / location
  * Skip if structure doesn't yield a title + company

Hitmarker is gaming-specific so industry score will be high for most
listings — that means even imperfect parsing is better than missing
the source entirely.
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


@register("hitmarker")
class HitmarkerScraper(BaseScraper):
    source_name = "hitmarker"
    schedule = "cron(30 6 * * ? *)"
    rate_limit_rps = 0.5

    BASE_URL = "https://hitmarker.net"
    LIST_URL = "https://hitmarker.net/jobs"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    DEFAULT_MAX_PAGES = 3

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
            # Hitmarker emits both relative ("/jobs/<slug>") and absolute
            # ("https://hitmarker.net/jobs/<slug>") hrefs depending on the
            # template. Use a regex that matches both shapes — and reject
            # the bare /jobs root + category filter URLs (querystring only).
            _job_href_re = re.compile(
                r"^(?:https?://(?:www\.)?hitmarker\.net)?/jobs/[^/?#]+/?$"
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
                # The <a> IS the card on Hitmarker (each card = one anchor
                # whose children are the title/company/location divs).
                # Using anchor.parent here would grab the whole grid div +
                # every sibling card, breaking dedup and parse alignment.
                yield {
                    "_href": href,
                    "_url":  urljoin(self.BASE_URL, href),
                    "_html": str(anchor),
                }
            if new_count == 0:
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        href = payload.get("_href") or ""
        url  = payload.get("_url") or ""
        if not href or not url:
            return None

        soup = BeautifulSoup(payload.get("_html") or "", "html.parser")

        # Title: Hitmarker uses <div class="font-bold ..."> not headings.
        # Try font-bold first, then fall back to h2/h3/h4 (in case the
        # template ever changes back to semantic headings).
        title_node = soup.find(class_=re.compile(r"\bfont-bold\b"))
        if title_node is None:
            title_node = soup.find(["h2", "h3", "h4"])
        title = _clean(title_node.get_text(" ", strip=True)) if title_node else ""

        # Company + location: scan leaf text nodes (no nested element children).
        # First short text after the title is the company; second is location.
        # Hitmarker also emits employer logos with alt text — try alt first.
        company = ""
        location = None
        # Try logo alt-text path first (most reliable when present).
        # Hitmarker's alt is "<Company> logo" — strip the trailing " logo".
        logo_img = soup.find("img", alt=True)
        if logo_img and logo_img.get("alt") and len(logo_img["alt"]) < 80:
            company = _clean(logo_img["alt"])
            company = re.sub(r"\s+logo$", "", company, flags=re.I)

        # Fall back to leaf-tag text scan.
        for tag in soup.find_all(["p", "span", "div"]):
            if tag.find(["p", "span", "div", "h1", "h2", "h3", "h4"]):
                continue
            text = _clean(tag.get_text(" ", strip=True))
            if not text or text == title or len(text) > 80:
                continue
            looks_like_location = (
                "remote" in text.lower
                or re.search(r",\s*[A-Z]{2}\b", text)
                or re.search(r",\s*[A-Z][a-z]+", text)        # e.g. ", United Kingdom"
            )
            if looks_like_location and not location:
                location = text
                continue
            if not company:
                company = text
                continue
            if company and location:
                break

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
            elif "onsite" in lower or "on-site" in lower:
                remote = False

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
