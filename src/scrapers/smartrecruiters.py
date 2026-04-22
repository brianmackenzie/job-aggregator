"""SmartRecruiters ATS scraper.

For each company in config/companies.yaml with ats == "smartrecruiters",
fetches open job postings from SmartRecruiters' public API.

SmartRecruiters exposes two unauthenticated endpoints:

  1. LIST   GET https://api.smartrecruiters.com/v1/companies/{id}/postings
            ?offset=N&limit=100
            Returns a shallow metadata page (id, name, location, release
            date). `totalFound` tells us how many postings exist.

  2. DETAIL GET https://api.smartrecruiters.com/v1/companies/{id}/postings/{pid}
            Returns the full record — description HTML, applyUrl, postingUrl,
            compensation block, etc.

Descriptions are needed by the scoring engine, so the scraper always calls
DETAIL for each posting it keeps. To avoid hammering the API when a high-
volume tenant has 1500+ postings, we cap each company at `max_jobs`
(default 250, configurable per-company via the `smartrecruiters:` block in
companies.yaml — mirrors the Workday block).

SmartRecruiters' published rate limit is 5 req/sec per IP for the public
endpoints; we stay well under that with rate_limit_rps = 2.0 for safety.
At 250 postings per company that's roughly (3 list pages + 250 detail
calls) / 2 rps ≈ 125 seconds per company — comfortably inside the 900s
Lambda ceiling for a handful of SmartRecruiters companies.

Error handling mirrors GreenhouseScraper:
  - 404 on the company id → company left SmartRecruiters; warn + continue.
  - 404 on a single posting detail → listing removed between list and
    detail; warn, skip that one posting, continue with the rest.
  - Other errors on list → re-raise (BaseScraper records the run).
  - Other errors on a single detail → caught per-item by scrape_run.
"""
import re
from typing import Iterable, Optional

import requests

from scrapers.ats_companies import load_ats_companies
from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


# Match the helper in greenhouse.py. Kept inline (rather than a shared util)
# because the needs of each scraper are tiny and copying keeps base.py clean.
def _strip_html(html: str) -> str:
    """Strip tags + common HTML entities, collapse whitespace.

    SmartRecruiters returns every section as HTML. We concatenate all
    sections then run this once so keyword matching and DynamoDB storage
    both see plain text.
    """
    text = re.sub(r"<[^>]+>", " ", html or "")
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&#\d+;", " ", text)
    return re.sub(r"\s+", " ", text).strip


def _join_jobad_sections(jobad: dict) -> str:
    """Concatenate every section of a SmartRecruiters jobAd into one
    plain-text block suitable for keyword scoring.

    The payload looks like:
        jobAd: {sections: {
            companyDescription: {title, text},
            jobDescription:     {title, text},
            qualifications:     {title, text},
            additionalInformation: {title, text},
        }}
    All fields are optional. `text` is HTML.
    """
    sections = (jobad or {}).get("sections") or {}
    chunks: list[str] = 
    # Deterministic order — keeps the stored description diff-stable
    # across re-fetches even though dict order is now insertion-stable.
    for key in (
        "companyDescription",
        "jobDescription",
        "qualifications",
        "additionalInformation",
    ):
        sec = sections.get(key) or {}
        html = sec.get("text") or ""
        if html:
            chunks.append(html)
    return _strip_html("\n".join(chunks))


@register("smartrecruiters")
class SmartRecruitersScraper(BaseScraper):
    source_name = "smartrecruiters"
    # Weekly — Mondays 07:00 UTC, alongside the other ATS scrapers.
    # Kept on the same weekly cadence so SmartRecruiters, Greenhouse, Lever,
    # Ashby, and Workday all land in the same scrape-runs digest.
    schedule    = "cron(0 7 ? * MON *)"
    # 2 req/sec — SmartRecruiters' published ceiling is ~5 rps; 2 leaves
    # headroom so a noisy neighbour on the same egress IP doesn't trip them.
    rate_limit_rps = 2.0

    API_BASE   = "https://api.smartrecruiters.com/v1/companies"
    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT
    # SmartRecruiters caps `limit` at 100. Three pages give us 300 postings
    # per company before the max_jobs cap kicks in — enough for all tier-1s.
    LIST_PAGE_SIZE    = 100
    # Default cap per company if companies.yaml doesn't set a tighter one.
    DEFAULT_MAX_JOBS  = 250

    def fetch(self) -> Iterable[dict]:
        """Yield enriched (list + detail) dicts, one per posting.

        Each yielded dict has:
          * the full DETAIL response (description, applyUrl, etc.)
          * a `_company_meta` key with the companies.yaml record (tier,
            industry, name) so parse can enrich the RawJob.
        """
        companies = load_ats_companies("smartrecruiters")
        if not companies:
            # No SmartRecruiters companies configured yet — harmless.
            from common.logging import log
            log.info("smartrecruiters_no_companies_configured")
            return

        for company in companies:
            # The SmartRecruiters company identifier (e.g. "AcmeCorp1",
            # "ExampleMediaInc") lives in `ats_slug` on the company row.
            # To find it: open the company's public SmartRecruiters careers
            # page and look at the URL path segment after "/company/" — that's
            # the ID.
            company_id = company.get("ats_slug")
            if not company_id:
                continue

            # Per-company max_jobs cap (default 250). Stored under the
            # optional `smartrecruiters:` block on the company record so a
            # single company can opt into a larger or smaller slice.
            sr_cfg    = company.get("smartrecruiters") or {}
            max_jobs  = int(sr_cfg.get("max_jobs") or self.DEFAULT_MAX_JOBS)

            yield from self._fetch_company(company, company_id, max_jobs)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fetch_company(
        self, company: dict, company_id: str, max_jobs: int
    ) -> Iterable[dict]:
        """Paginate the list endpoint, then fetch detail for each posting
        up to `max_jobs`. Yields the detail dicts with `_company_meta`.
        """
        from common.logging import log

        offset = 0
        seen   = 0

        while seen < max_jobs:
            list_url = f"{self.API_BASE}/{company_id}/postings"
            params = {
                "offset": offset,
                "limit":  min(self.LIST_PAGE_SIZE, max_jobs - seen),
            }

            self._throttle
            try:
                resp = requests.get(
                    list_url,
                    params=params,
                    headers={"User-Agent": self.USER_AGENT},
                    timeout=30,
                )
            except Exception as exc:
                raise RuntimeError(
                    f"SmartRecruiters list failed for company_id={company_id!r} "
                    f"({company.get('name')}): {exc}"
                ) from exc

            if resp.status_code == 404:
                # Wrong company id, or the board was taken down.
                log.warn(
                    "smartrecruiters_company_not_found",
                    company_id=company_id,
                    company=company.get("name"),
                    hint=f"Verify at https://jobs.smartrecruiters.com/{company_id}",
                )
                return
            resp.raise_for_status
            data = resp.json

            page = data.get("content") or 
            if not page:
                # Clean end-of-list — SmartRecruiters returns empty content
                # once offset exceeds totalFound.
                return

            for item in page:
                if seen >= max_jobs:
                    return

                posting_id = item.get("id")
                if not posting_id:
                    continue

                detail = self._fetch_detail(company_id, posting_id)
                if detail is None:
                    # Couldn't retrieve the full record — skip quietly.
                    # Per-item scrape_run error handling would also let
                    # us yield the list row and parse would bail on
                    # missing description, but this keeps malformed rows
                    # out of the raw S3 archive.
                    continue

                detail["_company_meta"] = company
                seen += 1
                yield detail

            # Advance. `totalFound` lets us short-circuit when we've drained
            # the board below max_jobs (avoids one trailing empty page).
            offset += len(page)
            total = data.get("totalFound") or 0
            if offset >= total:
                return

    def _fetch_detail(
        self, company_id: str, posting_id: str
    ) -> Optional[dict]:
        """Fetch one posting's detail record. Returns None on any failure
        so the caller can skip cleanly — we don't want a single removed
        listing to abort the whole company."""
        from common.logging import log

        url = f"{self.API_BASE}/{company_id}/postings/{posting_id}"
        self._throttle
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": self.USER_AGENT},
                timeout=30,
            )
        except Exception as exc:
            log.warn(
                "smartrecruiters_detail_net_err",
                company_id=company_id,
                posting_id=posting_id,
                error=str(exc),
            )
            return None

        if resp.status_code == 404:
            # Listing vanished between the list and detail calls — common
            # when a posting closes mid-crawl. Not an error.
            log.info(
                "smartrecruiters_detail_missing",
                company_id=company_id,
                posting_id=posting_id,
            )
            return None
        if not resp.ok:
            log.warn(
                "smartrecruiters_detail_http_err",
                company_id=company_id,
                posting_id=posting_id,
                status=resp.status_code,
            )
            return None

        try:
            return resp.json
        except Exception as exc:
            log.warn(
                "smartrecruiters_detail_json_err",
                company_id=company_id,
                posting_id=posting_id,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Parse
    # ------------------------------------------------------------------

    def parse(self, payload: dict) -> Optional[RawJob]:
        """Convert one SmartRecruiters DETAIL payload into a RawJob.

        Key fields used:
          id            → native_id (prefixed with the SR company id to
                          keep job_ids unique across tenants)
          name          → title
          location      → {city, region, country, remote, hybrid, fullLocation}
          postingUrl    → url (public, unauthenticated view)
          applyUrl      → fallback url if postingUrl missing
          jobAd.sections.* → concatenated + HTML-stripped description
          releasedDate  → posted_at (ISO8601)
          _company_meta → our companies.yaml enrichment
        """
        posting_id = payload.get("id")
        title      = (payload.get("name") or "").strip
        if not posting_id or not title:
            return None

        meta         = payload.get("_company_meta", {})
        company_id   = meta.get("ats_slug", "unknown")
        company_name = meta.get("name") or (payload.get("company") or {}).get("name") or ""
        if not company_name:
            # Without a company we can't populate CompanyIndex GSI — skip.
            return None

        # ---- Location -------------------------------------------------
        # SR returns a rich object. Prefer fullLocation; fall back to
        # "city, region, country". If everything is absent the field is
        # left None (the scoring engine handles missing locations).
        loc_obj  = payload.get("location") or {}
        location = loc_obj.get("fullLocation") or None
        if not location:
            parts = [
                loc_obj.get("city"),
                loc_obj.get("region"),
                loc_obj.get("country"),
            ]
            joined = ", ".join(p for p in parts if p)
            location = joined or None

        # Remote flag: SR has an explicit boolean. We honour it.
        # `hybrid: true` is treated as non-remote for scoring purposes —
        # the scoring engine has its own hybrid keyword detection.
        remote = None
        if "remote" in loc_obj:
            remote = bool(loc_obj.get("remote"))

        # ---- Description ---------------------------------------------
        description = _join_jobad_sections(payload.get("jobAd") or {})
        description = description or None

        # ---- URL -----------------------------------------------------
        # postingUrl is the canonical public page. applyUrl is the same
        # URL with `?oga=true` appended. Either works; postingUrl is the
        # cleaner share link.
        url = payload.get("postingUrl") or payload.get("applyUrl") or ""

        # ---- Posted-at -----------------------------------------------
        # SR returns ISO8601 with millisecond precision.
        # BaseScraper.normalize runs canonicalize_posted_at on it.
        posted_at = payload.get("releasedDate") or None

        return RawJob(
            native_id   = f"{company_id}:{posting_id}",
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
        """Inject company_tier into the stored row for scoring modifiers."""
        row = super.normalize(job)
        if job.raw:
            tier = job.raw.get("company_tier")
            if tier:
                row["company_tier"] = tier
        return row
