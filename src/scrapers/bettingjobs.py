"""BettingJobs scraper — Applyflow JSON API.

BettingJobs (https://www.bettingjobs.com) is the largest specialist
recruiter for the iGaming / sportsbook industry. Highest signal-per-job
ratio of any source for the user's iGaming track.

# History

The first attempt at this scraper (2026-04-XX) tried to parse the
public listing HTML at https://www.bettingjobs.com/jobs and discovered
the page is rendered by a Vue widget — the initial HTML contains no
job content. We disabled the source rather than ship a 0-yield scraper.

# Re-enable approach

Reverse-engineering the Vue widget revealed it talks to the Applyflow
SaaS backend at:

    https://account-api-uk.applyflow.com/api/seeker/v1/search-job

…with four required identification headers pulled from `window.afConfig`
on the host page:

    job-buckets:    BETTING-JOBS
    seeker-buckets: betting-jobs
    platform-code:  applyflow
    site-code:      bettingjobs

The endpoint returns a clean JSON envelope:

    {
      "search_results": {
        "job_count": 191,
        "jobs": [ {…rich record…}, … ]
      },
      "search_filters": {…},
      "meta": {…}
    }

Each job record carries: uuid, job_title, company_name, job_description,
location_label, location_state_code, pay_min/max + pay_currency,
created_at (ISO), apply_url, and a slug `URL` we use to construct the
public detail page.

This API is undocumented — Applyflow could change it at any time.
We pin the four headers to the values discovered on the live page so
that if Applyflow rotates them we get a clean 500 ("No job bucket")
in CloudWatch and the per-source try/except keeps the daily run alive.

# Pagination

The API takes `page` (1-indexed) and `resultsPerPage`. We default to
50 results per page and walk up to `max_pages` (config default 5 = 250
jobs cap). The job count is well under that today (~191), so a single
run typically pulls the entire active listing.
"""
import json
import re
from typing import Iterable, Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.sources_config import load_source_config
from scrapers.user_agent import USER_AGENT as _USER_AGENT


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Quick-and-dirty HTML → text. We use BS for any record where the
    plain regex strip might leave entities; for short snippets the regex
    is enough."""
    if not html:
        return ""
    if "<" in html:
        # Use BS for robust handling of nested tags and entities.
        try:
            return _WHITESPACE_RE.sub(
                " ",
                BeautifulSoup(html, "html.parser").get_text(" ", strip=True),
            ).strip
        except Exception:
            return _WHITESPACE_RE.sub(" ", _HTML_TAG_RE.sub(" ", html)).strip
    return _WHITESPACE_RE.sub(" ", html).strip


def _coerce_int(v) -> Optional[int]:
    """Pay fields are sometimes strings, sometimes ints, sometimes
    empty. Return a positive int or None."""
    if v is None or v == "" or v == "null":
        return None
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


@register("bettingjobs")
class BettingJobsScraper(BaseScraper):
    source_name = "bettingjobs"
    schedule = "cron(30 6 * * ? *)"      # 06:30 UTC daily — same slot as other HTML/RSS sources
    rate_limit_rps = 0.5

    PUBLIC_BASE_URL = "https://www.bettingjobs.com"
    API_URL = "https://account-api-uk.applyflow.com/api/seeker/v1/search-job"

    # Headers harvested from window.afConfig on bettingjobs.com/jobs.
    # If Applyflow ever rotates these, the API returns 500 "No job bucket"
    # and the scraper produces 0 jobs (logged as a successful 0-yield run,
    # not a hard failure).
    BUCKET_HEADERS = {
        "job-buckets":    "BETTING-JOBS",
        "seeker-buckets": "betting-jobs",
        "platform-code":  "applyflow",
        "site-code":      "bettingjobs",
    }

    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    # The Applyflow API silently caps at 20 results per page regardless of
    # what you pass in `resultsPerPage`, so to capture the full ~191-job
    # feed we need ~10+ pages. We default to 12 (240 cap) for headroom.
    DEFAULT_MAX_PAGES        = 12
    DEFAULT_RESULTS_PER_PAGE = 50

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        max_pages        = int(cfg.get("max_pages")        or self.DEFAULT_MAX_PAGES)
        results_per_page = int(cfg.get("results_per_page") or self.DEFAULT_RESULTS_PER_PAGE)

        # Same headers every request — build once outside the loop.
        headers = {
            "User-Agent": self.USER_AGENT,
            "Accept":     "application/json, text/plain, */*",
            "Origin":     self.PUBLIC_BASE_URL,
            "Referer":    f"{self.PUBLIC_BASE_URL}/jobs/",
            **self.BUCKET_HEADERS,
        }

        seen_uuids: set[str] = set
        for page in range(1, max_pages + 1):
            self._throttle
            try:
                resp = requests.get(
                    self.API_URL,
                    headers=headers,
                    params={"page": page, "resultsPerPage": results_per_page},
                    timeout=30,
                )
                resp.raise_for_status
                envelope = resp.json
            except (requests.RequestException, ValueError, json.JSONDecodeError):
                # Stop pagination, don't fail the whole run. The base
                # class records a partial-run row in ScrapeRuns.
                break

            search_results = envelope.get("search_results") or {}
            jobs           = search_results.get("jobs") or 
            if not jobs:
                # Past the end of the listing.
                break

            new_count = 0
            for j in jobs:
                uuid = j.get("uuid") or j.get("id")
                if not uuid or uuid in seen_uuids:
                    continue
                seen_uuids.add(uuid)
                new_count += 1
                yield j

            # Defensive early-stop: if a page returned only repeats
            # (unlikely with this API, but safer) we bail out.
            if new_count == 0:
                break
            # Also stop if we've consumed the full job_count.
            total = search_results.get("job_count") or 0
            if total and len(seen_uuids) >= total:
                break

    def parse(self, payload: dict) -> Optional[RawJob]:
        uuid       = payload.get("uuid") or payload.get("id")
        title      = (payload.get("job_title") or "").strip
        company    = (payload.get("company_name") or "").strip or "BettingJobs"
        if not uuid or not title:
            return None

        # URL fields: `URL` is a slug like 'french-social-media-specialist/<uuid>'.
        # The public detail page lives at `/jobs/<URL>/`.
        url_slug = (payload.get("URL") or "").strip
        public_url = (
            f"{self.PUBLIC_BASE_URL}/jobs/{url_slug}/"
            if url_slug else f"{self.PUBLIC_BASE_URL}/jobs/{uuid}/"
        )

        # Description: prefer job_description; fall back to job_body
        # (which is the same content but HTML-formatted) then short_description.
        # Strip HTML in any case so the algo scorer sees clean text.
        description = (
            payload.get("job_description")
            or payload.get("job_body")
            or payload.get("short_description")
            or ""
        )
        description = _strip_html(description) or None

        # Location: location_label is the human-readable form
        # ("France, Remote", "London, UK", "Malta", etc.).
        location = (payload.get("location_label") or "").strip or None

        # Remote inference: location_state_code starts with "remote-" when
        # the role is remote, e.g. "remote-france". Belt-and-braces with the
        # location_label substring check.
        state_code = (payload.get("location_state_code") or "").lower
        loc_lower  = location.lower if location else ""
        if "remote" in loc_lower or state_code.startswith("remote"):
            remote = True
        elif "onsite" in loc_lower or "on-site" in loc_lower:
            remote = False
        else:
            remote = None

        # Pay: prefer the *_norm fields (Applyflow's annualized values);
        # fall back to raw pay_min/pay_max if those are blank.
        salary_min = _coerce_int(payload.get("pay_min_norm")) or _coerce_int(payload.get("pay_min"))
        salary_max = _coerce_int(payload.get("pay_max_norm")) or _coerce_int(payload.get("pay_max"))
        # Sanity floor — any value under 20k is almost certainly hourly,
        # daily, or per-week noise we don't want feeding the scorer.
        if salary_min is not None and salary_min < 20_000:
            salary_min = None
        if salary_max is not None and salary_max < 20_000:
            salary_max = None

        # Posted-at: created_at is ISO-8601 with microseconds + Z. The
        # algo gates accept any ISO-prefixed string; pass through verbatim.
        posted_at = payload.get("created_at") or payload.get("activates_at") or None

        return RawJob(
            native_id=str(uuid),
            title=title,
            company=company,
            url=public_url,
            location=location,
            description=description,
            posted_at=posted_at,
            salary_min=salary_min,
            salary_max=salary_max,
            remote=remote,
            raw=payload,
        )
