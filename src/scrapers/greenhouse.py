"""Greenhouse ATS scraper.

For each company in config/companies.yaml with ats == "greenhouse", fetches
all open job listings from Greenhouse's public board API:

    GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

`content=true` includes the full HTML job description so the scoring engine
can do keyword matching on it. Descriptions are stripped of HTML before storage.

Rate limiting: 1 req/sec across all company fetches. Greenhouse has no
documented public rate limit, but throttling avoids being blocked.

Error handling:
  - 404 for a slug → company left Greenhouse; logged as warning, not error.
  - Any other fetch exception re-raised; BaseScraper.scrape_run catches it
    and records an error ScrapeRuns row for this source while continuing.
  - Per-item parse errors are also caught by BaseScraper.scrape_run.
"""
import re
from typing import Iterable, Optional

import requests

from scrapers.ats_companies import load_ats_companies
from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace to produce plain text.

    Greenhouse job descriptions are full HTML. We strip them so:
    1. Keyword matching works on human-readable text.
    2. DynamoDB doesn't store multi-MB blobs of markup.
    """
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", " ", text)     # numeric HTML entities like &#8203;
    return re.sub(r"\s+", " ", text).strip


@register("greenhouse")
class GreenhouseScraper(BaseScraper):
    source_name = "greenhouse"
    schedule    = "cron(0 7 ? * MON *)"   # Weekly — Mondays 07:00 UTC
    rate_limit_rps = 1.0                  # One HTTP request per second

    API_BASE   = "https://boards-api.greenhouse.io/v1/boards"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        """For each Greenhouse company in companies.yaml, fetch all open jobs.

        Each yielded payload is a Greenhouse job dict enriched with a
        `_company_meta` key containing the companies.yaml record. parse
        uses this to set company name, tier, and industry without a DB lookup.
        """
        companies = load_ats_companies("greenhouse")
        if not companies:
            # Zero Greenhouse-hosted companies is a valid config state — the
            # scraper should no-op gracefully rather than error the run.
            from common.logging import log
            log.info("greenhouse_no_companies_configured")
            return

        for company in companies:
            slug = company.get("ats_slug")
            if not slug:
                continue  # ats_slug is null — skip (likely a Workday company)

            url = f"{self.API_BASE}/{slug}/jobs"
            self._throttle
            try:
                resp = requests.get(
                    url,
                    params={"content": "true"},
                    headers={"User-Agent": self.USER_AGENT},
                    # 15s per request (was 30). With 29 GH companies and the
                    # Lambda's 900s wall clock, 30s × 29 = 870s left zero margin
                    # the 2026-04-19 Monday run actually timed out the worker.
                    # 15s × 29 = 435s leaves ~half the budget for the rest of
                    # the pipeline (parse + DDB writes + S3 archive).
                    timeout=15,
                )
                if resp.status_code == 404:
                    # Company has left Greenhouse or the slug is wrong.
                    # Log a warning but continue to the next company.
                    from common.logging import log
                    log.warn(
                        "greenhouse_slug_not_found",
                        slug=slug,
                        company=company.get("name"),
                        hint="Verify at boards.greenhouse.io/" + slug,
                    )
                    continue
                resp.raise_for_status
                data = resp.json
            except Exception as exc:
                # Per the "never hard-fail a scrape run" rule (CLAUDE.md), a
                # single bad company must NOT kill the other 28. Log + skip
                # the run keeps going and the rest land normally. (Earlier
                # versions re-raised, which on 2026-04-19 caused a 900s Lambda
                # timeout and the entire weekly Greenhouse sweep produced
                # zero ScrapeRuns rows.)
                from common.logging import log
                log.warn(
                    "greenhouse_fetch_failed",
                    slug=slug,
                    company=company.get("name"),
                    error=str(exc),
                )
                continue

            for job in data.get("jobs", ):
                # Attach company YAML record so parse can read tier / industry.
                job["_company_meta"] = company
                yield job

    def parse(self, payload: dict) -> Optional[RawJob]:
        """Convert one Greenhouse job payload into a RawJob.

        Greenhouse API fields used:
          id            → native_id (prefixed with slug to prevent cross-company collisions)
          title         → title
          location.name → location
          updated_at    → posted_at (Greenhouse may not expose created_at publicly)
          absolute_url  → url
          content       → description (HTML stripped to plain text)
          _company_meta → enrichment from fetch: name, tier, industry
        """
        job_id = payload.get("id")
        title  = (payload.get("title") or "").strip
        if not job_id or not title:
            return None  # Malformed listing — skip silently

        meta         = payload.get("_company_meta", {})
        slug         = meta.get("ats_slug", "unknown")
        company_name = meta.get("name") or ""

        # Location can be a nested object {"name": "New York, NY"} or a string.
        loc_raw  = payload.get("location") or {}
        location = (
            loc_raw.get("name") if isinstance(loc_raw, dict) else str(loc_raw)
        ) or None

        # Strip HTML from the job description.
        description = _strip_html(payload.get("content") or "") or None

        # Greenhouse returns ISO8601 timestamps in updated_at; canonicalize_posted_at
        # in BaseScraper.normalize handles the conversion.
        posted_at = payload.get("updated_at") or None

        return RawJob(
            # Prefix with slug: "acme-corp:123456" → job_id "greenhouse:acme-corp:123456"
            native_id   = f"{slug}:{job_id}",
            title       = title,
            company     = company_name,
            url         = payload.get("absolute_url", ""),
            location    = location,
            description = description,
            posted_at   = posted_at,
            remote      = None,  # Greenhouse has no structured remote flag; scoring engine infers it
            raw         = {
                # Pass tier + industry through to the normalize override below.
                "company_tier": meta.get("tier"),
                "industry":     meta.get("industry"),
            },
        )

    def normalize(self, job: RawJob) -> dict:
        """Extend BaseScraper.normalize to inject company_tier.

        company_tier is used by the scoring engine's modifier stack:
          Tier S → +10,  Tier 1 → +6,  Tier 2 → +3.
        Setting it at scrape time avoids a DynamoDB lookup per job.
        """
        row = super.normalize(job)
        if job.raw:
            tier = job.raw.get("company_tier")
            if tier:
                row["company_tier"] = tier
        return row
