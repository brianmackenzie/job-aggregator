"""Ashby ATS scraper.

For each company in config/companies.yaml with ats == "ashby", fetches all
open job postings from Ashby's public job board API:

    GET https://api.ashbyhq.com/posting-api/job-board/{slug}

Ashby's public API returns a JSON object whose top-level key is "jobs"
(NOT "jobPostings" — earlier Ashby docs used that name, but the live
shape as of 2026-04 is `{"jobs": [...], "apiVersion": ...}`). Each job
exposes id/title/location/publishedAt/applyUrl/descriptionPlain/etc.
No auth token is required for public job boards.

Rate limiting: 1 req/sec across all company fetches.

Error handling mirrors the other ATS scrapers — 404 on unknown slug → warning
+ continue; other errors logged + re-raised.
"""
from typing import Iterable, Optional

import requests

from scrapers.ats_companies import load_ats_companies
from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


@register("ashby")
class AshbyScraper(BaseScraper):
    source_name = "ashby"
    schedule    = "cron(0 7 ? * MON *)"   # Weekly — Mondays 07:00 UTC
    rate_limit_rps = 1.0

    API_BASE   = "https://api.ashbyhq.com/posting-api/job-board"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        """For each Ashby company in companies.yaml, fetch all open postings.

        Live Ashby API shape (verified 2026-04 against jobs.ashbyhq.com/openai):
            GET /posting-api/job-board/{slug}
            → {"jobs": [{id, title, department, team, employmentType,
                         location, secondaryLocations, publishedAt,
                         isRemote, workplaceType, applyUrl,
                         descriptionPlain, descriptionHtml, ...}],
               "apiVersion": "..."}
        """
        companies = load_ats_companies("ashby")
        if not companies:
            # Zero Ashby-hosted companies is a valid config state — after the
            # Phase-5 slug verification pass, all previous Ashby candidates
            # were moved to `ats: null` pending a HTML scraper. Log it
            # and return; do NOT raise (never hard-fail a scrape run).
            from common.logging import log
            log.info("ashby_no_companies_configured")
            return

        for company in companies:
            slug = company.get("ats_slug")
            if not slug:
                continue

            url = f"{self.API_BASE}/{slug}"
            self._throttle
            try:
                resp = requests.get(
                    url,
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=30,
                )
                if resp.status_code == 404:
                    from common.logging import log
                    log.warn(
                        "ashby_slug_not_found",
                        slug=slug,
                        company=company.get("name"),
                        hint="Verify at jobs.ashbyhq.com/" + slug,
                    )
                    continue
                resp.raise_for_status
                data = resp.json
            except Exception as exc:
                # Per the "never hard-fail a scrape run" rule (CLAUDE.md),
                # a single bad company must NOT kill the rest. Log + skip.
                from common.logging import log
                log.warn(
                    "ashby_fetch_failed",
                    slug=slug,
                    company=company.get("name"),
                    error=str(exc),
                )
                continue

            # Ashby wraps postings in a "jobs" key (NOT "jobPostings" — older
            # Ashby docs used that name, but the live API as of 2026-04 returns
            # `{"jobs": [...], "apiVersion": ...}`). Defensive: accept either.
            postings = data.get("jobs") or data.get("jobPostings") or 
            if not isinstance(postings, list):
                from common.logging import log
                log.warn(
                    "ashby_unexpected_response",
                    slug=slug,
                    keys=list(data.keys) if isinstance(data, dict) else None,
                )
                continue

            # Log per-company counts so an empty board is visible in CloudWatch
            # rather than silently disappearing into a 0-jobs scrape run row.
            from common.logging import log
            log.info(
                "ashby_company_fetched",
                slug=slug,
                company=company.get("name"),
                postings=len(postings),
            )

            for posting in postings:
                posting["_company_meta"] = company
                yield posting

    def parse(self, payload: dict) -> Optional[RawJob]:
        """Convert one Ashby posting into a RawJob.

        Ashby API fields used (live shape 2026-04):
          id               → native_id (slug-prefixed)
          title            → title
          location         → location  (NOT locationName — older docs were wrong)
          isRemote         → remote
          descriptionPlain → description (Ashby always provides plain text)
          applyUrl         → url
          publishedAt      → posted_at (ISO8601 string or null)
          _company_meta    → enrichment from fetch
        """
        posting_id = payload.get("id")
        title      = (payload.get("title") or "").strip
        if not posting_id or not title:
            return None

        meta         = payload.get("_company_meta", {})
        slug         = meta.get("ats_slug", "unknown")
        company_name = meta.get("name") or ""

        # Ashby provides a location string and a boolean remote flag. Field is
        # `location` on the live API; fall back to `locationName` defensively.
        location = payload.get("location") or payload.get("locationName") or None
        is_remote = payload.get("isRemote")
        remote   = bool(is_remote) if is_remote is not None else None

        # Plain text description — Ashby includes this on all postings.
        description = (payload.get("descriptionPlain") or "").strip or None

        # publishedAt is ISO8601 or None. BaseScraper.normalize calls
        # canonicalize_posted_at which handles both.
        posted_at = payload.get("publishedAt") or None

        # Ashby's applyUrl is the canonical apply link.
        url = payload.get("applyUrl") or ""

        return RawJob(
            native_id   = f"{slug}:{posting_id}",
            title       = title,
            company     = company_name,
            url         = url,
            location    = location,
            description = description,
            posted_at   = posted_at,
            remote      = remote,
            raw         = {
                "company_tier": meta.get("tier"),
                "industry":     meta.get("industry"),
            },
        )

    def normalize(self, job: RawJob) -> dict:
        """Inject company_tier into the row for scoring modifiers."""
        row = super.normalize(job)
        if job.raw:
            tier = job.raw.get("company_tier")
            if tier:
                row["company_tier"] = tier
        return row
