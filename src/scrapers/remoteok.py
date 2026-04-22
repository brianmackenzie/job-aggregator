"""RemoteOK scraper.

API: GET https://remoteok.com/api -> JSON array.

Notes:
  * The first element of the array is a legal-notice / metadata object
    without an `id` field. We skip it.
  * RemoteOK is by definition remote — we hardcode remote=True.
  * They ask for a User-Agent that identifies you (so they can email
    if you abuse the API).
"""
from typing import Iterable, Optional

import requests

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


@register("remoteok")
class RemoteOKScraper(BaseScraper):
    source_name = "remoteok"
    schedule = "cron(0 6 * * ? *)"      # 06:00 UTC daily
    rate_limit_rps = 1.0

    API_URL = "https://remoteok.com/api"
    # RemoteOK's docs ask for an identifying User-Agent. Centralised in
    # scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        # Single GET — small JSON payload, no pagination.
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
        for entry in data:
            # Skip the legal-notice header (no `id`) and any non-dict noise.
            if isinstance(entry, dict) and entry.get("id"):
                yield entry

    def parse(self, payload: dict) -> Optional[RawJob]:
        # `position` is RemoteOK's term for the job title.
        title = payload.get("position") or payload.get("title")
        company = payload.get("company")
        if not title or not company:
            return None
        # Prefer epoch over date string when both are present (epoch is
        # more reliable to parse).
        posted_at = payload.get("epoch") or payload.get("date")
        return RawJob(
            native_id=str(payload["id"]),
            title=title,
            company=company,
            url=payload.get("url") or payload.get("apply_url") or "",
            location=payload.get("location") or "Remote",
            description=payload.get("description"),
            posted_at=str(posted_at) if posted_at else None,
            salary_min=payload.get("salary_min"),
            salary_max=payload.get("salary_max"),
            remote=True,
            raw=payload,
        )
