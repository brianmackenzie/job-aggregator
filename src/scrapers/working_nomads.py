"""Working Nomads scraper — JSON feed.

Working Nomads publishes a structured JSON feed of all currently-listed
remote jobs at:
    https://www.workingnomads.com/api/exposed_jobs/

Response is a JSON array; each entry has keys like:
    {
      "id": 12345,
      "title": "Senior Backend Engineer",
      "company_name": "Acme Corp",
      "url": "https://www.workingnomads.com/jobs/...",
      "location": "Anywhere",
      "category_name": "Programming",
      "tags": "python, postgres, remote",
      "pub_date": "2026-04-16T12:34:56",
      "description": "Long HTML body..."
    }

By definition every Working Nomads listing is remote, so we hardcode
remote=True. Some listings include a US/EU restriction in `location`
which we surface verbatim.
"""
import html
import re
from typing import Iterable, Optional

import requests

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(s: str) -> str:
    """JSON descriptions are HTML. Strip + decode + collapse whitespace."""
    if not s:
        return ""
    text = _TAG_RE.sub(" ", s)
    text = html.unescape(text)
    return _WHITESPACE_RE.sub(" ", text).strip


@register("working_nomads")
class WorkingNomadsScraper(BaseScraper):
    source_name = "working_nomads"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily, with HTML batch
    rate_limit_rps = 1.0

    API_URL = "https://www.workingnomads.com/api/exposed_jobs/"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        # Single GET — feed is a flat array, no pagination.
        self._throttle
        resp = requests.get(
            self.API_URL,
            headers={
                "User-Agent": self.USER_AGENT,
                "Accept": "application/json",
            },
            timeout=30,
        )
        resp.raise_for_status
        data = resp.json
        # Defensive: feed is normally a list but tolerate a {"results": [...]} wrap.
        if isinstance(data, dict):
            data = data.get("results") or data.get("jobs") or 
        for entry in data:
            if isinstance(entry, dict):
                yield entry

    def parse(self, payload: dict) -> Optional[RawJob]:
        title   = (payload.get("title") or "").strip
        company = (payload.get("company_name") or payload.get("company") or "").strip
        if not title or not company:
            return None

        # Working Nomads numeric `id` is unique. Fall back to the URL slug.
        native_id = str(payload.get("id") or "").strip
        if not native_id:
            url_for_id = payload.get("url") or ""
            native_id = url_for_id.rstrip("/").rsplit("/", 1)[-1] or ""
        if not native_id:
            return None

        return RawJob(
            native_id=native_id,
            title=title,
            company=company,
            url=(payload.get("url") or "").strip,
            location=(payload.get("location") or "").strip or "Remote",
            description=_strip_html(payload.get("description") or ""),
            posted_at=(payload.get("pub_date") or "").strip or None,
            remote=True,                  # WN is remote-only
            raw=payload,
        )
