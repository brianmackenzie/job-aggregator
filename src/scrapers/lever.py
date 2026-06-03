"""Lever ATS scraper.

For each company in config/companies.yaml with ats == "lever", fetches all
open job postings from Lever's public API:

    GET https://api.lever.co/v0/postings/{slug}?mode=json&limit=500

Lever's v0 API is unauthenticated for public postings. `mode=json` returns
structured JSON instead of HTML. Descriptions come as both HTML and plain text;
we prefer `descriptionPlain` for keyword matching.

Rate limiting: 1 req/sec across all company fetches.

Error handling mirrors GreenhouseScraper — 404 on unknown slug → warning +
continue; other errors logged + re-raised so BaseScraper records them.
"""
from typing import Iterable, Optional

import requests

from scrapers.ats_companies import load_ats_companies
from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


@register("lever")
class LeverScraper(BaseScraper):
    source_name = "lever"
    schedule    = "cron(0 7 ? * MON *)"   # Weekly — Mondays 07:00 UTC, same as Greenhouse
    rate_limit_rps = 1.0

    API_BASE   = "https://api.lever.co/v0/postings"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    def fetch(self) -> Iterable[dict]:
        """For each Lever company in companies.yaml, fetch all open postings.

        Each yielded payload is a Lever posting dict enriched with
        `_company_meta` (the companies.yaml record). Lever returns a JSON
        array directly (unlike Greenhouse's wrapper object).
        """
        companies = load_ats_companies("lever")
        if not companies:
            # Zero Lever-hosted companies is a valid config state.
            from common.logging import log
            log.info("lever_no_companies_configured")
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
                    params={"mode": "json", "limit": 500},
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=30,
                )
                if resp.status_code == 404:
                    from common.logging import log
                    log.warn(
                        "lever_slug_not_found",
                        slug=slug,
                        company=company.get("name"),
                        hint="Verify at jobs.lever.co/" + slug,
                    )
                    continue
                resp.raise_for_status
                postings = resp.json
            except Exception as exc:
                # Per the "never hard-fail a scrape run" rule (CLAUDE.md),
                # a single bad company must NOT kill the rest. Log + skip.
                from common.logging import log
                log.warn(
                    "lever_fetch_failed",
                    slug=slug,
                    company=company.get("name"),
                    error=str(exc),
                )
                continue

            # Lever returns a JSON array at the top level.
            if not isinstance(postings, list):
                from common.logging import log
                log.warn(
                    "lever_unexpected_response",
                    slug=slug,
                    type=type(postings).__name__,
                )
                continue

            for posting in postings:
                posting["_company_meta"] = company
                yield posting

    def parse(self, payload: dict) -> Optional[RawJob]:
        """Convert one Lever posting into a RawJob.

        Lever API fields used:
          id                 → native_id (slug-prefixed)
          text               → title
          categories.team    → (ignored — we use Lever's categories for location)
          categories.location → location
          descriptionPlain   → description (preferred; falls back to description HTML)
          hostedUrl          → url
          createdAt          → posted_at (epoch milliseconds)
          _company_meta      → enrichment from fetch
        """
        posting_id = payload.get("id")
        title      = (payload.get("text") or "").strip
        if not posting_id or not title:
            return None

        meta         = payload.get("_company_meta", {})
        slug         = meta.get("ats_slug", "unknown")
        company_name = meta.get("name") or ""

        # Location is in categories.location (a string like "New York, NY").
        categories = payload.get("categories") or {}
        location   = categories.get("location") or None

        # Prefer plain text description to avoid HTML noise in keyword matching.
        # Fall back to the HTML field if plain is absent (older postings).
        description = (
            payload.get("descriptionPlain")
            or payload.get("description")
            or ""
        ).strip or None

        # Lever timestamps are epoch milliseconds; canonicalize_posted_at
        # in BaseScraper.normalize handles int / float / ISO8601 strings.
        created_ms = payload.get("createdAt")
        posted_at  = str(created_ms) if created_ms is not None else None

        # Lever sometimes includes a `commitment` field ("Full-time", etc.)
        # and a `workplaceType` in newer API versions ("remote", "hybrid").
        workplace = (payload.get("workplaceType") or "").lower
        remote    = True if workplace == "remote" else (
                    False if workplace == "onsite" else None
                    )

        return RawJob(
            native_id   = f"{slug}:{posting_id}",
            title       = title,
            company     = company_name,
            url         = payload.get("hostedUrl", ""),
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
