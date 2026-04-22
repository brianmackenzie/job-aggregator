"""Apify LinkedIn scraper — .

Calls the Apify REST API to run a LinkedIn jobs scraper actor (default:
bebity/linkedin-jobs-scraper) once per configured search URL, then yields
each job item from the resulting datasets.

Why Apify (not direct LinkedIn scraping):
  - LinkedIn's public guest API (no login = no account-ban risk)
  - Pay-per-result (~$0.005/job) keeps cost ~$22/month at our defaults
  - Actor is swappable via sources.yaml if bebity ever breaks

Lifecycle of one Apify run:
  1. POST /v2/acts/{actor}/runs?token=...      -> {data: {id, defaultDatasetId, ...}}
  2. GET  /v2/actor-runs/{run_id}?token=...    -> poll until status=SUCCEEDED
  3. GET  /v2/datasets/{dataset_id}/items?token=...  -> list of job dicts

We start every search's run first (POST in a loop), THEN poll them all in
parallel. This keeps total wall-clock close to the slowest single run
rather than the sum of all runs — critical for staying under Lambda's
300s timeout.

Token: stored in SSM at /jobs-aggregator/apify_token (set by the operator via
`aws ssm put-parameter --name /jobs-aggregator/apify_token ...`). Read
through common.secrets.get_secret which has a 5-minute in-process cache.

Field mapping (Apify item -> RawJob):
  jobUrl OR url OR applyUrl    -> url           (try several keys; varies by actor version)
  id OR jobId                  -> native_id     (LinkedIn's numeric job id; canonical)
  title                        -> title
  companyName                  -> company
  location                     -> location
  description OR descriptionText -> description
  salary                       -> salary_min/max  (parsed best-effort from a free-text string)
  postedTime OR postedAt OR publishedAt -> posted_at
  workplaceType OR workType    -> remote (string match)

Errors:
  - SSM token unavailable    -> raises (BaseScraper records errored ScrapeRuns row)
  - One search fails to start -> log warn, continue with the others
  - Run never reaches SUCCEEDED before poll_timeout -> log warn, abandon that run
  - Per-item parse errors    -> caught upstream by BaseScraper.scrape_run
"""
from __future__ import annotations

import re
import time
from typing import Iterable, Optional

import requests

from common.logging import log
from common.secrets import get_secret
from scrapers.base import BaseScraper, RawJob
from scrapers.registry import register
from scrapers.sources_config import load_source_config


# ---------- Salary string parsing ------------------------------------------

# LinkedIn surfaces salary as a free-text string when the company supplies
# it: "$250,000 - $310,000", "$250K - $310K", "USD 250000-310000", etc.
# We extract the two largest dollar amounts and treat them as min/max.
_SALARY_NUM_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*([kKmM]?)")


def _parse_salary(s: str | None) -> tuple[Optional[int], Optional[int]]:
    """Best-effort parse of LinkedIn's free-text salary blob.

    Returns (min, max) as ints in dollars/year, or (None, None) if we
    can't find at least two numbers. Numeric suffixes K and M scale up.
    Hourly rates (with "/hour" or "/hr") are filtered out so we don't
    score "$45/hour" as a $45/year salary.
    """
    if not s:
        return (None, None)
    text = s.lower
    if "/hour" in text or "/hr" in text or "per hour" in text or "hourly" in text:
        return (None, None)

    nums: list[int] = 
    for raw, suffix in _SALARY_NUM_RE.findall(s):
        try:
            n = float(raw.replace(",", ""))
        except ValueError:
            continue
        if suffix.lower == "k":
            n *= 1_000
        elif suffix.lower == "m":
            n *= 1_000_000
        nums.append(int(n))

    # Drop trivially-small numbers (e.g. "401k plan" → 401000 is a false hit
    # only matters when no real salary is present; require >= $20K to count
    # as a candidate salary number).
    nums = [n for n in nums if n >= 20_000]
    if len(nums) < 2:
        return (None, None)
    nums.sort
    return (nums[0], nums[-1])


# ---------- Apify HTTP helpers ---------------------------------------------

_API_BASE = "https://api.apify.com/v2"


def _start_run(
    actor_id: str,
    token:    str,
    payload:  dict,
    *,
    label:    str = "",
) -> Optional[dict]:
    """POST a run-start to Apify. Returns the `data` dict on success, None on failure.

    `payload` is whatever actor-input dict the caller has already built. The
    scraper supports TWO input shapes for the bebity actor:

      (legacy, URL-based) {"searchUrl": "...", "count": 50, "scrapeCompany": False}
        — used by broad keyword searches in sources.yaml `searches.url`

      (structured, recommended) {"companyName": ["Acme Corp"],
                                 "experienceLevel": ["Director", "Executive"],
                                 "publishedAt": "Past 24 hours",
                                 "rows": 50, ...}
        — used by the per-company pinned searches (sources.yaml
          `searches.input`). Structured input routes through LinkedIn's
          native company-resolver instead of keyword boolean matching,
          which collapses the 98% drift we saw on the URL-keyword form.

    `label` is only used in the failure log to help identify which search
    broke without leaking the full payload. Pass the `name` from sources.yaml.
    """
    url = f"{_API_BASE}/acts/{actor_id}/runs"
    try:
        r = requests.post(
            url,
            params={"token": token},
            json=payload,
            timeout=30,
        )
        r.raise_for_status
        return r.json.get("data") or {}
    except Exception as exc:
        # Caller decides whether to keep going (we just log + return None).
        log.warn(
            "apify_run_start_failed",
            actor=actor_id,
            search=label,
            error=f"{type(exc).__name__}: {exc}",
        )
        return None


def _get_run_status(run_id: str, token: str) -> str:
    """Return Apify run status. Codes: READY, RUNNING, SUCCEEDED, FAILED,
    ABORTING, ABORTED, TIMING-OUT, TIMED-OUT."""
    r = requests.get(
        f"{_API_BASE}/actor-runs/{run_id}",
        params={"token": token},
        timeout=15,
    )
    r.raise_for_status
    return (r.json.get("data") or {}).get("status") or "UNKNOWN"


def _fetch_dataset_items(dataset_id: str, token: str) -> list[dict]:
    """Page-flat fetch of all items in an Apify dataset. Apify returns a JSON
    array directly; for our small per-search datasets (≤50 items each)
    pagination isn't needed."""
    r = requests.get(
        f"{_API_BASE}/datasets/{dataset_id}/items",
        params={"token": token, "format": "json", "clean": "true"},
        timeout=60,
    )
    r.raise_for_status
    data = r.json
    return data if isinstance(data, list) else 


# ---------- The scraper -----------------------------------------------------

@register("apify_linkedin")
class ApifyLinkedInScraper(BaseScraper):
    source_name = "apify_linkedin"
    # EventBridge cron — daily at 12:00 UTC = 08:00 ET. Wired into
    # template.yaml's DailyApifyScrapeSchedule.
    schedule = "cron(0 12 * * ? *)"
    rate_limit_rps = 5.0   # plenty for Apify polling — they allow much more

    def fetch(self) -> Iterable[dict]:
        cfg = load_source_config(self.source_name)
        if not cfg.get("enabled", True):
            log.info("apify_disabled_in_config")
            return

        actor_id        = cfg.get("actor_id", "bebity~linkedin-jobs-scraper")
        token_param     = cfg.get("ssm_token_param", "apify_token")
        count_per_query = int(cfg.get("count_per_search", 50))
        poll_interval   = float(cfg.get("poll_interval_secs", 6))
        poll_timeout    = float(cfg.get("poll_timeout_secs", 240))
        searches        = cfg.get("searches") or 

        # ---- One-shot overrides --------------------------------------------
        # Set by scrape_worker when the lambda is invoked with an `overrides`
        # event payload (e.g. for the historical 30-day seed). Daily cron
        # invocations don't pass overrides so this block is a no-op.
        #
        # Recognized keys:
        #   count_per_search     int     bump max results per search URL
        #   f_TPR_override       str     replace the f_TPR=… param in every
        #                                search URL (e.g. "r2592000" = 30d).
        #                                Used by the one-shot historical seed
        #                                so the SAME search definitions can
        #                                pull the last 30d once without
        #                                changing the daily-cron config.
        #   only_searches        list    filter `searches` by name — useful
        #                                when seeding only a subset (e.g.
        #                                only the new per-company-pinned
        #                                searches, not the broad keyword set).
        ov = getattr(self, "overrides", {}) or {}
        if "count_per_search" in ov:
            count_per_query = int(ov["count_per_search"])
            log.info("apify_override_count", count_per_search=count_per_query)
        if ov.get("only_searches"):
            wanted = {n for n in ov["only_searches"]}
            searches = [s for s in searches if s.get("name") in wanted]
            log.info("apify_override_only_searches", kept=[s["name"] for s in searches])
        if ov.get("f_TPR_override"):
            new_tpr = ov["f_TPR_override"]
            # URL-based searches: swap the f_TPR query param. Structured-
            # input searches: swap `publishedAt` to the human-readable
            # equivalent LinkedIn expects in the actor's JSON schema.
            # This lets the historical-seed override reach both shapes
            # of searches uniformly.
            new_published_at = _f_tpr_to_published_at(new_tpr)
            patched = 
            for s in searches:
                s2 = dict(s)
                if s2.get("url"):
                    s2["url"] = _replace_f_tpr(s2["url"], new_tpr)
                if s2.get("input"):
                    # Don't mutate the original config; copy it so daily
                    # cron runs don't inherit a previous override.
                    inp = dict(s2["input"])
                    inp["publishedAt"] = new_published_at
                    s2["input"] = inp
                patched.append(s2)
            searches = patched
            log.info(
                "apify_override_f_tpr",
                f_TPR=new_tpr,
                published_at=new_published_at,
            )

        if not searches:
            log.warn("apify_no_searches_configured")
            return

        # Fetch the token once per scrape. Raises if SSM is unreachable —
        # BaseScraper.scrape_run catches and records the errored run.
        token = get_secret(token_param)

        # ---- start every run, capture (search, run_id, dataset_id)
        # We do this serially (one POST per search) but it's fast — each POST
        # returns in ~200ms once Apify accepts the run.
        started: list[dict] = 
        for s in searches:
            self._throttle
            payload = _build_actor_payload(s, count_per_query)
            if payload is None:
                log.warn(
                    "apify_search_misconfigured",
                    search=s.get("name"),
                    hint="needs either `url` or `input` key",
                )
                continue
            data = _start_run(actor_id, token, payload, label=s.get("name", ""))
            if not data:
                continue   # _start_run already logged the failure
            started.append({
                "search_name": s["name"],
                "run_id":      data["id"],
                "dataset_id":  data["defaultDatasetId"],
                "started_at":  time.monotonic,
            })
            log.info(
                "apify_run_started",
                search=s["name"],
                run_id=data["id"],
                dataset_id=data["defaultDatasetId"],
                mode="structured" if s.get("input") else "url",
            )

        if not started:
            log.warn("apify_no_runs_started", search_count=len(searches))
            return

        # ---- poll all runs in parallel until each finishes (or times out)
        pending  = {r["run_id"]: r for r in started}
        finished: list[dict] = 
        while pending:
            for run_id in list(pending.keys):
                run = pending[run_id]
                # Per-run timeout — give up rather than blocking the Lambda.
                if time.monotonic - run["started_at"] > poll_timeout:
                    log.warn(
                        "apify_run_timed_out",
                        search=run["search_name"],
                        run_id=run_id,
                        elapsed=round(time.monotonic - run["started_at"], 1),
                    )
                    pending.pop(run_id)
                    continue
                self._throttle
                try:
                    status = _get_run_status(run_id, token)
                except Exception as exc:
                    log.warn(
                        "apify_status_poll_failed",
                        run_id=run_id,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                    continue
                if status in ("SUCCEEDED",):
                    log.info("apify_run_succeeded", search=run["search_name"], run_id=run_id)
                    finished.append(run)
                    pending.pop(run_id)
                elif status in ("FAILED", "ABORTED", "TIMED-OUT", "TIMING-OUT", "ABORTING"):
                    log.warn(
                        "apify_run_finished_bad",
                        search=run["search_name"],
                        run_id=run_id,
                        status=status,
                    )
                    pending.pop(run_id)
                # else: still RUNNING / READY — leave in pending and re-check.

            if pending:
                time.sleep(poll_interval)

        # ---- pull each successful dataset and yield each item.
        # We tag each item with `_search_name` so parse can use it as a
        # disambiguating prefix in the slug-style native_id.
        for run in finished:
            try:
                items = _fetch_dataset_items(run["dataset_id"], token)
            except Exception as exc:
                log.warn(
                    "apify_dataset_fetch_failed",
                    search=run["search_name"],
                    dataset_id=run["dataset_id"],
                    error=f"{type(exc).__name__}: {exc}",
                )
                continue
            log.info(
                "apify_dataset_fetched",
                search=run["search_name"],
                items=len(items),
            )
            for item in items:
                item["_search_name"] = run["search_name"]
                yield item

    def parse(self, payload: dict) -> Optional[RawJob]:
        """Convert one Apify LinkedIn item to a RawJob.

        Apify actors normalize most fields, but key names drift between actor
        versions (jobUrl vs url vs applyUrl, etc.). We try multiple aliases.
        """
        # -- ID extraction. Try several keys; LinkedIn's numeric job ID
        # sometimes appears in `id`, sometimes only in the URL path.
        native_id = (
            payload.get("id")
            or payload.get("jobId")
            or payload.get("trackingUrn")
            or _extract_id_from_url(
                payload.get("jobUrl") or payload.get("url") or payload.get("applyUrl") or ""
            )
        )
        title = (payload.get("title") or "").strip
        if not native_id or not title:
            return None

        company = (
            payload.get("companyName")
            or payload.get("company")
            or ""
        ).strip
        # Skip rows with no company. Without it the downstream PutItem
        # fails on the CompanyIndex GSI ("empty string for key attribute"),
        # which surfaces as a per-row error in ScrapeRuns. Apify
        # occasionally returns rows scraped from staffing-agency reposts
        # where the original company isn't in the LinkedIn payload.
        if not company:
            return None

        url = (
            payload.get("jobUrl")
            or payload.get("url")
            or payload.get("applyUrl")
            or ""
        )

        location    = payload.get("location") or None
        description = (
            payload.get("description")
            or payload.get("descriptionText")
            or payload.get("descriptionHtml")
            or None
        )

        # -- Posted-at: Apify items vary on this field name + format.
        posted_at = (
            payload.get("postedTime")
            or payload.get("postedAt")
            or payload.get("publishedAt")
            or payload.get("postedDate")
            or None
        )

        # -- Salary: free-text on most actors. Best-effort parse.
        smin, smax = _parse_salary(payload.get("salary"))

        # -- Remote inference. LinkedIn's workplaceType is the most reliable;
        # a few actors expose it as `workType`. Fallback: substring match on
        # location ("Remote", "Anywhere").
        wt = (
            payload.get("workplaceType")
            or payload.get("workType")
            or ""
        ).lower
        remote: Optional[bool] = None
        if wt == "remote":
            remote = True
        elif wt in ("on-site", "onsite", "on_site"):
            remote = False
        elif location and "remote" in location.lower:
            remote = True

        return RawJob(
            # Native LinkedIn job ID is globally unique — no search-slug
            # prefix, otherwise the same job returned by overlapping searches
            # gets written 2-3 times (the `_search_name` tag is preserved in
            # the raw S3 archive for debugging).
            native_id   = str(native_id),
            title       = title,
            company     = company,
            url         = url,
            location    = location,
            description = description,
            posted_at   = posted_at,
            salary_min  = smin,
            salary_max  = smax,
            remote      = remote,
        )


# ---------- helpers --------------------------------------------------------

# LinkedIn job URLs look like https://www.linkedin.com/jobs/view/3945102348/
# Extract the numeric ID as a fallback when the actor doesn't surface it.
_LI_JOB_ID_RE = re.compile(r"/jobs/(?:view|search)/(\d+)")


def _extract_id_from_url(url: str) -> str:
    if not url:
        return ""
    m = _LI_JOB_ID_RE.search(url)
    return m.group(1) if m else ""


# f_TPR=rNNN is LinkedIn's "time posted" (seconds-back) parameter. We
# replace it for the one-shot historical seed (30d=2592000) without
# touching the daily-cron config. If the URL has no f_TPR yet, append it.
_F_TPR_RE = re.compile(r"([?&])f_TPR=[^&]*")


def _replace_f_tpr(url: str, new_value: str) -> str:
    """Swap the f_TPR=… query param for `new_value` (e.g. "r2592000").

    If the URL already has f_TPR, replace it in place (preserving order).
    Otherwise, append it with the right separator so the rest of the
    search semantics are untouched.
    """
    if not url:
        return url
    if _F_TPR_RE.search(url):
        return _F_TPR_RE.sub(rf"\1f_TPR={new_value}", url)
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}f_TPR={new_value}"


# bebity's actor schema (verified 2026-04-18 via /v2/acts/.../builds):
#   publishedAt enum = ['', 'r2592000', 'r604800', 'r86400']
# LinkedIn's URL `f_TPR` parameter uses the SAME r-prefixed second-counts,
# so no translation is needed. We just pass the value straight through.
# An empty string means "any time" (no filter).
_F_TPR_VALID = {"", "r86400", "r604800", "r2592000"}


def _f_tpr_to_published_at(f_tpr: str) -> str:
    """Map a LinkedIn f_TPR value (e.g. `r2592000`) onto the bebity
    actor's `publishedAt` field. The actor's enum exactly matches
    LinkedIn's URL syntax, so for recognized values this is a passthrough.
    Unrecognized values fall back to '' (any time) — safer than silently
    filtering to the wrong slice."""
    return f_tpr if f_tpr in _F_TPR_VALID else ""


def _build_actor_payload(search: dict, default_count: int) -> Optional[dict]:
    """Turn a sources.yaml search entry into the actor-input dict to POST.

    Two shapes are accepted (use whichever suits the query):

      1. URL-based (legacy, used by broad keyword searches):
           { name: ..., url: "https://www.linkedin.com/jobs/search/?..." }
         We send {"searchUrl": url, "count": N, "scrapeCompany": false}.

      2. Structured (new, used by per-company pinned searches):
           { name: ..., input: {companyName: [...], experienceLevel: [...],
                                 publishedAt: "Past 24 hours", ...} }
         We pass `input` straight through, adding the count field the
         actor expects (bebity calls this one `rows`).

    Returns None if the search entry has neither `url` nor `input` — caller
    logs the misconfiguration and skips it.
    """
    if search.get("url"):
        return {
            "searchUrl":     search["url"],
            "count":         default_count,
            "scrapeCompany": False,
        }
    if search.get("input"):
        # Copy so we don't stomp the shared sources.yaml dict between runs.
        payload = dict(search["input"])
        # bebity uses `rows` for the result cap in structured-input mode,
        # `count` for URL-search mode. Only set `rows` if the caller
        # hasn't already — some pinned searches may want a lower cap.
        payload.setdefault("rows", default_count)
        # Default `publishedAt` to "r86400" (last 24h) for daily cron if
        # the search didn't pin one — mirrors URL-mode f_TPR=r86400.
        # The actor's enum uses the same r-second-count strings as
        # LinkedIn's f_TPR URL param.
        payload.setdefault("publishedAt", "r86400")
        return payload
    return None
