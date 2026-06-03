"""Himalayas scraper.

API: GET https://himalayas.app/jobs/api?limit=50&offset=N

Returns a JSON object with a `jobs` array. We page through until the
array comes back empty or we hit a hard cap (Himalayas can return
thousands; we cap at a few hundred per run to keep things bounded).
"""
from typing import Iterable, Optional

import requests

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register


@register("himalayas")
class HimalayasScraper(BaseScraper):
    source_name = "himalayas"
    schedule = "cron(0 6 * * ? *)"
    rate_limit_rps = 1.0

    API_URL = "https://himalayas.app/jobs/api"
    PAGE_SIZE = 50
    MAX_PAGES = 8                       # cap at ~400 jobs per run

    def fetch(self) -> Iterable[dict]:
        for page in range(self.MAX_PAGES):
            self._throttle
            resp = requests.get(
                self.API_URL,
                params={"limit": self.PAGE_SIZE, "offset": page * self.PAGE_SIZE},
                headers={"Accept": "application/json"},
                timeout=30,
            )
            resp.raise_for_status
            payload = resp.json
            jobs = payload.get("jobs") or payload.get("data") or 
            if not jobs:
                break
            for job in jobs:
                yield job
            if len(jobs) < self.PAGE_SIZE:
                # Short page = last page.
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        # Himalayas has shifted field names over time; check the most
        # likely ones in order. Returning None when essentials are
        # missing skips the row instead of crashing the run.
        native_id = (
            payload.get("guid")
            or payload.get("id")
            or payload.get("slug")
        )
        title = payload.get("title")
        company = (
            payload.get("companyName")
            or payload.get("company_name")
            or payload.get("company")
        )
        url = (
            payload.get("applicationLink")
            or payload.get("url")
            or payload.get("link")
        )
        if not (native_id and title and company and url):
            return None

        location = (
            payload.get("locationRestrictions")
            or payload.get("location")
            or "Remote"
        )
        if isinstance(location, list):
            # Himalayas sometimes returns a list of country codes.
            location = ", ".join(str(x) for x in location) or "Remote"

        return RawJob(
            native_id=str(native_id),
            title=title,
            company=company,
            url=url,
            location=location,
            description=payload.get("description") or payload.get("excerpt"),
            posted_at=payload.get("pubDate") or payload.get("postedAt") or payload.get("published"),
            salary_min=payload.get("minSalary") or payload.get("salary_min"),
            salary_max=payload.get("maxSalary") or payload.get("salary_max"),
            remote=True,                # Himalayas is a remote-only board.
            raw=payload,
        )
