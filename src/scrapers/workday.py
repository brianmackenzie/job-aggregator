"""Workday scraper — . Flagged fragile in CLAUDE.md.

Public Workday job boards expose a JSON "cxs" API that powers their
careers-site front-ends. The shape is consistent across tenants, but
each tenant has its own base URL of the form:

    https://{subdomain}.{wdN}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs

where `subdomain` is usually the tenant short-name (e.g. "acme"),
`wdN` is the data-center cluster (wd1 / wd5 / wd103 / ...), and
`{site}` is the public site key chosen by the customer (e.g. "AcmeCareers").

Discovery: open the careers page in a browser, watch the Network tab —
the front-end POSTs to `/wday/cxs/{tenant}/{site}/jobs` and the URL
in the address bar is `{base_url}/{site}/...`. Fill those three fields
into the `workday:` block on the company in `config/companies.yaml`.

This scraper reads its tenant list from companies.yaml entries with
`ats: workday` and a populated `workday:` block:

    - name: "Acme Corp"
      ats: workday
      workday:
        base_url: "https://acme.wd1.myworkdayjobs.com"
        tenant:   "acme"
        site:     "AcmeCareers"
        max_jobs: 250         # optional; default 250

Per-tenant errors (404 → wrong URL, 403 → bot-blocked, 5xx, JSON decode
failure) are LOGGED + SWALLOWED. The scraper continues on to the next
tenant. This honors the "never hard-fail a scrape" rule (CLAUDE.md) and
matches how Greenhouse / Lever handle 404s.

We do NOT fetch detail pages. The list endpoint already gives us title,
location, externalPath (for the click-through URL), and a relative
"Posted N days ago" string. Hitting the per-job detail endpoint would
double the request volume and many tenants 403 / rate-limit on it.
The scoring engine works fine with title + company + location alone;
the description is just a bonus signal.

Workday's "postedOn" field is a human-readable string ("Posted Today",
"Posted Yesterday", "Posted 5 Days Ago", "Posted 30+ Days Ago"). We
approximate the actual timestamp by subtracting the number of days from
"now". Workday does not expose the precise post timestamp publicly.
"""
import re
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import requests

from common.logging import log
from scrapers.ats_companies import load_ats_companies
from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.user_agent import USER_AGENT as _USER_AGENT


# Per-tenant cap. 250 = ~13 paginated requests at 20/page. Large
# enterprise tenants can have 5000+ open postings; without a cap we'd
# blow the Lambda timeout and the scoring budget on jobs that mostly
# score 0.
_DEFAULT_MAX_JOBS_PER_TENANT = 250

# Workday's `limit` parameter is documented up to 20 on most public
# tenants. Larger values are silently clamped, so set it explicitly
# rather than trusting the server-side default.
_PAGE_SIZE = 20

# Matches "5 Days Ago", "30+ Days Ago" (with optional plus sign + spaces).
_REL_DAYS_RE = re.compile(r"(\d+)\+?\s*Day", re.IGNORECASE)


def _parse_posted_on(s: str) -> Optional[str]:
    """Convert Workday's relative "Posted X" string to an ISO8601 stamp.

    Workday list payloads carry strings like "Posted Today",
    "Posted Yesterday", "Posted 5 Days Ago", "Posted 30+ Days Ago".
    We approximate (today, today-1d, today-5d, today-30d). Returns None
    if the format is unrecognized so canonicalize_posted_at in
    BaseScraper.normalize falls back to the scrape-run timestamp.
    """
    if not s:
        return None
    low = s.strip.lower
    if "today" in low:
        delta_days = 0
    elif "yesterday" in low:
        delta_days = 1
    else:
        m = _REL_DAYS_RE.search(s)
        if not m:
            return None
        delta_days = int(m.group(1))
    posted = datetime.now(timezone.utc) - timedelta(days=delta_days)
    return posted.strftime("%Y-%m-%dT%H:%M:%SZ")


@register("workday")
class WorkdayScraper(BaseScraper):
    source_name    = "workday"
    # Weekly, alongside Greenhouse / Lever / Ashby — the per-tenant volume
    # is similar and Workday tenants don't update minute-to-minute.
    schedule       = "cron(0 7 ? * MON *)"
    rate_limit_rps = 1.0   # 1 req/sec across all tenants — gentle on Workday infra

    # Centralised in scrapers/user_agent.py — reads contact_email from
    # config/sources.yaml:scraper_defaults. Change that field, not this line.
    USER_AGENT = _USER_AGENT

    # ----- fetch -----------------------------------------------------------

    def fetch(self) -> Iterable[dict]:
        """Walk every Workday-tagged company in companies.yaml and page
        through its public job listings. Yields one payload per posting.

        Each yielded dict is the raw Workday `jobPostings` entry plus two
        enrichment keys: `_company_meta` (the companies.yaml record) and
        `_workday` (base_url / tenant / site so parse can build the
        click-through URL without re-reading the YAML).
        """
        companies = load_ats_companies("workday")
        if not companies:
            # No Workday tenants configured yet is a valid state — this
            # source is opt-in. Bail without error so the run is "ok".
            log.info("workday_no_companies_configured")
            return

        for company in companies:
            wd = company.get("workday") or {}
            base_url = (wd.get("base_url") or "").rstrip("/")
            tenant   = wd.get("tenant")
            site     = wd.get("site")
            if not (base_url and tenant and site):
                # Misconfigured entry — log a warning so it shows up in
                # the next health.html review, but do not abort the run.
                log.warn(
                    "workday_misconfigured",
                    company=company.get("name"),
                    has_base_url=bool(base_url),
                    has_tenant=bool(tenant),
                    has_site=bool(site),
                )
                continue

            max_jobs = wd.get("max_jobs", _DEFAULT_MAX_JOBS_PER_TENANT)
            yield from self._page_through(
                company, base_url, tenant, site, max_jobs,
            )

    # ----- pagination ------------------------------------------------------

    def _page_through(
        self,
        company:  dict,
        base_url: str,
        tenant:   str,
        site:     str,
        max_jobs: int,
    ) -> Iterable[dict]:
        """POST `/wday/cxs/{tenant}/{site}/jobs` repeatedly with a growing
        offset until the server returns fewer than _PAGE_SIZE postings,
        we hit the per-tenant cap, or an HTTP/JSON error occurs.

        Errors are logged and swallowed (return early) so one bad tenant
        does not break the rest of the run.
        """
        endpoint = f"{base_url}/wday/cxs/{tenant}/{site}/jobs"
        # Browsers send these on the XHR — some tenants 403 without them.
        # Workday's CDN sniffs Origin/Referer to verify the request is
        # coming from the same careers site that mounted the JS widget.
        headers = {
            "User-Agent":   self.USER_AGENT,
            "Accept":       "application/json",
            "Content-Type": "application/json",
            "Origin":       base_url,
            "Referer":      f"{base_url}/{site}",
        }
        company_label = company.get("name") or tenant
        offset = 0
        seen   = 0

        while seen < max_jobs:
            self._throttle
            try:
                resp = requests.post(
                    endpoint,
                    json={
                        "appliedFacets": {},
                        "limit":         _PAGE_SIZE,
                        "offset":        offset,
                        "searchText":    "",
                    },
                    headers=headers,
                    timeout=30,
                )
            except Exception as exc:
                # Network-level error (DNS, connect, read timeout). Log
                # and move on to the next tenant — running with what we
                # already yielded is fine.
                log.warn(
                    "workday_request_failed",
                    company=company_label,
                    endpoint=endpoint,
                    error=str(exc),
                )
                return

            if resp.status_code == 404:
                # The base_url/tenant/site combo doesn't resolve — most
                # likely the careers site moved or the slug was wrong.
                log.warn(
                    "workday_endpoint_not_found",
                    company=company_label,
                    endpoint=endpoint,
                    hint="Verify base_url/tenant/site in companies.yaml",
                )
                return
            if resp.status_code in (401, 403, 429):
                # Auth required, bot-blocked, or throttled. Nothing the
                # scraper can do at runtime — skip this tenant.
                log.warn(
                    "workday_blocked_or_throttled",
                    company=company_label,
                    status=resp.status_code,
                )
                return
            if not resp.ok:
                log.warn(
                    "workday_unexpected_status",
                    company=company_label,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                return

            try:
                data = resp.json
            except Exception as exc:
                log.warn(
                    "workday_json_decode_failed",
                    company=company_label,
                    error=str(exc),
                    body=resp.text[:300],
                )
                return

            postings = data.get("jobPostings") or 
            if not postings:
                # Reached the end of the tenant's listings.
                return

            for posting in postings:
                # Enrich each posting so parse doesn't need the YAML.
                posting["_company_meta"] = company
                posting["_workday"] = {
                    "base_url": base_url,
                    "tenant":   tenant,
                    "site":     site,
                }
                yield posting
                seen += 1
                if seen >= max_jobs:
                    return

            # Short page = last page. Common-case early exit so we don't
            # do one extra round-trip just to learn there are no more.
            if len(postings) < _PAGE_SIZE:
                return
            offset += _PAGE_SIZE

    # ----- parse -----------------------------------------------------------

    def parse(self, payload: dict) -> Optional[RawJob]:
        """Turn one Workday jobPostings entry into a RawJob.

        Required fields:
          title          → title (skip the row if missing or blank)
          externalPath   → path appended to {base_url}/{site} for the URL
                           (also used as native_id fallback)

        Optional fields:
          locationsText  → location (e.g., "Redwood City, CA")
          bulletFields[0]→ requisition ID like "R12345" — preferred native_id
          postedOn       → relative "Posted X Days Ago" → ISO8601 approximation
        """
        title         = (payload.get("title") or "").strip
        external_path = payload.get("externalPath") or ""
        if not title or not external_path:
            return None

        meta = payload.get("_company_meta", {})
        wd   = payload.get("_workday", {})

        company_name = meta.get("name") or ""
        base_url     = wd.get("base_url", "")
        site         = wd.get("site", "")
        tenant       = wd.get("tenant", "unknown")

        # Prefer Workday's req-ID as the native_id — it's what the company
        # itself uses internally and won't change if the title is edited.
        # Fallback: the terminal segment of externalPath (e.g. "Vice-…_R123").
        bullets = payload.get("bulletFields") or 
        req_id  = (bullets[0].strip if bullets and isinstance(bullets[0], str) else "")
        if not req_id:
            req_id = external_path.rsplit("/", 1)[-1] or external_path
        # tenant-prefix to prevent collisions if two tenants ever issue the
        # same R-number (each tenant's IDs are independent).
        native_id = f"{tenant}:{req_id}"

        # Public click-through URL. Both Origin and Referer use this domain,
        # so the URL is the same one a browser would land on.
        url = f"{base_url}/{site}{external_path}" if base_url and site else ""

        location  = (payload.get("locationsText") or "").strip or None
        posted_at = _parse_posted_on(payload.get("postedOn") or "")

        return RawJob(
            native_id   = native_id,
            title       = title,
            company     = company_name,
            url         = url,
            location    = location,
            description = None,    # Not in list response; see module docstring
            posted_at   = posted_at,
            remote      = None,    # Workday has no structured remote flag
            raw         = {
                "company_tier": meta.get("tier"),
                "industry":     meta.get("industry"),
            },
        )

    def normalize(self, job: RawJob) -> dict:
        """Inject company_tier so the scoring modifier stack picks it up.
        Mirrors the override in greenhouse.py / lever.py / ashby.py."""
        row = super.normalize(job)
        if job.raw:
            tier = job.raw.get("company_tier")
            if tier:
                row["company_tier"] = tier
        return row
