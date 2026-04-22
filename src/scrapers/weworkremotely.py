"""WeWorkRemotely scraper — RSS feed.

WeWorkRemotely publishes a category-keyed RSS feed at
    https://weworkremotely.com/categories/{category}/jobs.rss

The "all jobs" feed at https://weworkremotely.com/remote-jobs.rss exists
but is unstable / 503s frequently. We aggregate per-category feeds
instead, which mirrors what a user would see on the site.

Each <item> looks like:
    <item>
      <title>Acme Corp: Senior Engineer</title>
      <link>https://weworkremotely.com/remote-jobs/acme-corp-senior-engineer</link>
      <description>...HTML body...</description>
      <pubDate>Wed, 16 Apr 2026 12:34:56 +0000</pubDate>
      <guid>https://weworkremotely.com/remote-jobs/acme-corp-senior-engineer</guid>
      <region>...</region>   <!-- inconsistent — sometimes empty -->
      <category>Programming</category>
    </item>

Title format is consistently "<Company>: <Role>" with a colon separator.
We parse the company off the front and use the rest as the title.
By definition every WWR posting is remote, so we hardcode remote=True.
"""
import html
import re
from typing import Iterable, Optional
from xml.etree import ElementTree as ET

import requests

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.sources_config import load_source_config
from scrapers.user_agent import USER_AGENT as _USER_AGENT


# Default categories to scrape if config/sources.yaml doesn't override.
# These cover the most common executive / IC tracks; the full list of
# weworkremotely categories is at https://weworkremotely.com/categories.
# Override via config/sources.yaml -> weworkremotely.categories.
_DEFAULT_CATEGORIES = [
    "remote-programming-jobs",
    "remote-product-jobs",
    "remote-design-jobs",
    "remote-management-and-finance-jobs",
    "remote-marketing-jobs",
]

_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    """RSS descriptions are HTML. Strip tags + decode entities + collapse ws."""
    if not s:
        return ""
    text = _TAG_RE.sub(" ", s)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip


def _split_company_and_title(raw_title: str) -> tuple[str, str]:
    """WWR titles are "<Company>: <Role>". Split on the FIRST colon.

    Some posters use colons inside the role too ("Acme: Senior Engineer:
    Backend"), so a left-most split is the right call. If there's no
    colon at all, return ("", raw_title) and let the caller skip.
    """
    if ":" not in raw_title:
        return "", raw_title.strip
    company, _, role = raw_title.partition(":")
    return company.strip, role.strip


@register("weworkremotely")
class WeWorkRemotelyScraper(BaseScraper):
    """RSS scraper. One HTTP GET per category in config/sources.yaml."""

    source_name = "weworkremotely"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily, with the HTML batch
    rate_limit_rps = 1.0

    FEED_URL_TEMPLATE = "https://weworkremotely.com/categories/{slug}/jobs.rss"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        categories = cfg.get("categories") or _DEFAULT_CATEGORIES

        for slug in categories:
            self._throttle
            try:
                resp = requests.get(
                    self.FEED_URL_TEMPLATE.format(slug=slug),
                    headers={
                        "User-Agent": self.USER_AGENT,
                        "Accept": "application/rss+xml, application/xml",
                    },
                    timeout=30,
                )
                resp.raise_for_status
            except Exception:
                # Per-category failure must not abort the whole source.
                # Re-raise so the BaseScraper run loop logs it as a per-item
                # error against this category. Other categories continue.
                raise

            # Parse XML and yield one dict per <item>. We tag the category
            # so parse can stash it on the raw payload for debugging.
            try:
                root = ET.fromstring(resp.content)
            except ET.ParseError:
                # Malformed feed — skip this category, others may be fine.
                continue

            # RSS structure: <rss><channel><item>...</item>...</channel></rss>
            for item in root.findall(".//item"):
                payload = {child.tag: (child.text or "") for child in item}
                payload["_category"] = slug
                yield payload

    def parse(self, payload: dict) -> Optional[RawJob]:
        raw_title = (payload.get("title") or "").strip
        link = (payload.get("link") or payload.get("guid") or "").strip
        if not raw_title or not link:
            return None

        company, title = _split_company_and_title(raw_title)
        if not company or not title:
            # No "Company: Role" structure — skip rather than guess.
            return None

        # The link is also the guid which is also the de facto native_id.
        # The slug at the end is unique per posting.
        # https://weworkremotely.com/remote-jobs/<slug>
        native_id = link.rsplit("/", 1)[-1] or link

        description = _strip_html(payload.get("description") or "")
        location = (payload.get("region") or "").strip or "Remote"
        posted_at = (payload.get("pubDate") or "").strip or None

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=link,
            location=location,
            description=description,
            posted_at=posted_at,
            remote=True,                  # WWR is remote-only by definition
            raw=payload,
        )
