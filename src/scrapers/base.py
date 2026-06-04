"""BaseScraper — the engine for every scraper plugin.

Subclasses implement only:
    fetch()  -> Iterable[dict]           yield raw source payloads
    parse(p) -> Optional[RawJob]         convert one payload to a RawJob

Everything else — retry-free-of-care, rate limiting via self._throttle(),
per-item try/except, dedup via job_id, ScrapeRuns bookkeeping, and a
raw-payload archive to S3 — lives in `scrape_run()` on this class.

This is deliberate: the daily dispatcher will call scrape_run() on 20+
different sources, and one flaky source must never break the others.
By centralizing error handling here we guarantee that property.
"""
import gzip
import io
import json
import os
import re
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from html import unescape as _html_unescape
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Optional

import boto3

from common import db
from common.logging import log
from common.normalize import (
    build_job_id,
    canonicalize_posted_at,
    normalize_company,
    score_posted_sk,
)


def strip_html(html: Optional[str], max_chars: int = 12000) -> Optional[str]:
    """HTML (or plain text) -> collapsed plain text, capped at max_chars.

    Turns ATS/board job-description HTML into the plain-text `description`
    the scoring engine + QoL keyword scan expect. bs4 is imported lazily so
    base.py stays importable in minimal test envs; a regex tag-strip is the
    fallback. Returns None for empty/whitespace-only input so normalize()
    omits the field instead of storing an empty attribute.
    """
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup  # lazy: keep base.py import light
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return None
    return text[:max_chars]


def _iter_jsonld(node):
    """Yield every dict node in a parsed JSON-LD blob (handles top-level
    lists and @graph nesting)."""
    if isinstance(node, list):
        for x in node:
            yield from _iter_jsonld(x)
    elif isinstance(node, dict):
        yield node
        graph = node.get("@graph")
        if graph:
            yield from _iter_jsonld(graph)


def extract_job_description(html, css_selector=None, max_chars: int = 12000):
    """Pull a job description out of a detail page, robustly.

    Strategy, in order:
      1. JSON-LD `JobPosting.description` — the Google-for-Jobs structured
         data many boards embed. Stable across redesigns; preferred.
      2. A per-board CSS selector fallback (the JD container) for boards
         that don't emit JSON-LD.
    Returns plain text (via strip_html) or None. Best-effort: never raises.
    """
    if not html:
        return None
    try:
        from bs4 import BeautifulSoup  # lazy
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        return strip_html(html, max_chars)

    # 1) JSON-LD JobPosting.description
    for s in soup.find_all("script", type="application/ld+json"):
        raw = (s.string or s.get_text() or "").strip()
        if not raw or "JobPosting" not in raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        for node in _iter_jsonld(data):
            tp = node.get("@type")
            types = tp if isinstance(tp, list) else [tp]
            if "JobPosting" in types and node.get("description"):
                # JSON-LD descriptions are commonly HTML-entity-encoded
                # (&lt;p&gt;...). Unescape first so strip_html sees real tags
                # to strip, not literal angle-brackets in the text.
                d = strip_html(_html_unescape(node["description"]), max_chars)
                if d:
                    return d

    # 2) CSS selector fallback
    if css_selector:
        try:
            el = soup.select_one(css_selector)
        except Exception:
            el = None
        if el:
            d = strip_html(str(el), max_chars)
            if d:
                return d
    return None


@dataclass
class RawJob:
    """Source-agnostic intermediate form. Produced by parse(), consumed
    by BaseScraper.normalize() which maps it onto the Jobs table shape."""
    native_id: str
    title: str
    company: str
    url: str
    location: Optional[str] = None
    description: Optional[str] = None
    posted_at: Optional[str] = None
    salary_min: Optional[int] = None
    salary_max: Optional[int] = None
    remote: Optional[bool] = None
    raw: Optional[dict] = None


# ScrapeRuns rows are useful for debugging recent failures but not
# interesting after a month. TTL keeps the table small and health.html
# fast. The TTL attribute on the table is already enabled in template.yaml.
_SCRAPE_RUN_TTL_DAYS = 30


class BaseScraper(ABC):
    # Required on every subclass. Enforced in __init__.
    source_name: str = ""
    # EventBridge schedule expression, e.g. "cron(0 6 * * ? *)" or "rate(1 day)".
    # Read by the dispatcher when wiring EventBridge rules.
    schedule: str = ""
    # Max requests/sec inside fetch(). Subclasses call self._throttle() between
    # HTTP calls to respect this.
    rate_limit_rps: float = 1.0
    # Generic detail-page description enrichment (opt-in). When True,
    # scrape_run() fetches each job's detail page (RawJob.url) and extracts
    # the description (JSON-LD first, then _DESC_SELECTOR) whenever parse()
    # didn't already set one. For boards whose list/card payload omits the
    # JD. Bounded by max_desc_fetches/run; throttled; best-effort.
    auto_fetch_description: bool = False
    _DESC_SELECTOR = None             # CSS fallback used when JSON-LD absent
    max_desc_fetches: int = 1000      # per-run ceiling on detail fetches

    def __init__(self):
        if not self.source_name:
            raise ValueError(f"{type(self).__name__} must set source_name")
        self._last_request_at: float = 0.0
        self._s3_client = None

    # -------- abstract methods the subclass must implement -----------------

    @abstractmethod
    def fetch(self) -> Iterable[dict]:
        """Yield raw source payloads. Each one is handed to parse().
        Call self._throttle() between HTTP requests."""

    @abstractmethod
    def parse(self, payload: dict) -> Optional[RawJob]:
        """Convert a single raw payload to a RawJob. Return None to skip."""

    # -------- helpers subclasses may use -----------------------------------

    def _throttle(self) -> None:
        """Sleep long enough to keep our request rate at or below
        rate_limit_rps. Call this once per HTTP request inside fetch()."""
        if self.rate_limit_rps <= 0:
            return
        min_interval = 1.0 / self.rate_limit_rps
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _fetch_detail_description(self, url, css_selector=None,
                                 extra_headers=None):
        """GET a job's detail page and extract its description (JSON-LD
        first, then css_selector). Throttled; best-effort (any error ->
        None). Shared by board scrapers (live fetch) and the backfill
        script, so both extract descriptions identically.
        """
        if not url:
            return None
        import requests  # lazy: keep base.py importable without requests
        headers = {
            "User-Agent": getattr(self, "USER_AGENT", None) or (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-US,en;q=0.9",
        }
        if extra_headers:
            headers.update(extra_headers)
        self._throttle()
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            # requests defaults to ISO-8859-1 for text/html without a declared
            # charset, which mojibakes UTF-8 curly quotes; prefer the detected
            # encoding so the description text is clean.
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
        except Exception:
            return None
        return extract_job_description(resp.text, css_selector)

    def normalize(self, job: RawJob) -> dict:
        """RawJob -> Jobs-table row dict. Subclasses rarely override."""
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        posted_at = canonicalize_posted_at(job.posted_at) or now_iso
        row = {
            "job_id": build_job_id(self.source_name, job.native_id),
            "source": self.source_name,
            "native_id": job.native_id,
            "title": job.title,
            "company": job.company,
            "company_normalized": normalize_company(job.company),
            "url": job.url,
            "posted_at": posted_at,
            "scraped_at": now_iso,
            "status": "active",
            "track": "unscored",           # overwritten by the scoring engine
            "score": 0,
            "score_posted": score_posted_sk(0, posted_at),
        }
        # Only include optional fields when present — DynamoDB is happier
        # without empty-string attributes (some clients reject them).
        if job.location:               row["location"] = job.location
        if job.description:            row["description"] = job.description
        if job.remote is not None:     row["remote"] = job.remote
        if job.salary_min is not None: row["salary_min"] = job.salary_min
        if job.salary_max is not None: row["salary_max"] = job.salary_max
        return row

    # -------- scoring integration -------------------------------------------

    @staticmethod
    def _to_dynamo(obj):
        """Recursively convert Python floats to Decimal for DynamoDB.

        boto3's DynamoDB Table resource raises TypeError on plain floats.
        Converting via str() preserves the rounded representation without
        introducing binary-float precision issues.
        """
        if isinstance(obj, float):
            return Decimal(str(obj))
        if isinstance(obj, dict):
            return {k: BaseScraper._to_dynamo(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [BaseScraper._to_dynamo(i) for i in obj]
        return obj

    def _apply_score(self, row: dict) -> dict:
        """Run the combined (algo + Haiku semantic) scoring engine on a
        freshly normalised job row and write the results back before it is
        persisted to DynamoDB.

        Behaviour:
          * Always runs the algo engine — fast, deterministic, free.
          * If the row already exists in DynamoDB and has a recent
            semantic_score (within scoring.yaml::semantic.cache_days),
            we re-use the cached semantic value rather than calling the
            Anthropic API again. The caller is expected to have merged
            the existing row's semantic_* fields onto `row` before us
            (db.put_job preserves user state; semantic state is a
            separate concern handled here).
          * Otherwise calls Haiku unless the algo score is hard-gated
            (==0) or below scoring.yaml::semantic.skip_below_algo.

        Scoring is best-effort: if the engine raises for any reason we
        log a warning and keep the row with its default values
        (score=0, track="unscored") so the job still appears in the feed.

        We import lazily (inside the method) to keep the scraper module
        importable in test environments that don't have PyYAML or the
        anthropic SDK installed.
        """
        try:
            # Pull any existing semantic_* fields off the prior DynamoDB
            # row so the cache check inside score_combined() can work.
            # If this is a brand-new job there's nothing to merge.
            from common import db as _db
            existing = _db.get_job(row["job_id"])
            if existing:
                for k in (
                    "semantic_score", "semantic_rationale",
                    "semantic_scored_at", "semantic_model",
                    "work_mode",           # persist Haiku's work-mode label
                ):
                    if k in existing and k not in row:
                        row[k] = existing[k]

            from scoring.combined import score_combined
            result = score_combined(row, prefs={})

            row["score"]              = result["score"]
            row["algo_score"]         = result["algo_score"]
            row["track"]              = result["track"]
            # renamed DDB column hard_gates_hit →
            # gates_triggered so the export script and scoring engine
            # agree. The scoring layer's internal key has always been
            # `gates_triggered`; the DDB column was historically
            # `hard_gates_hit`, which made exports read an always-empty
            # field. Fix is a straight rename + one-shot migration
            # (see scripts/migrate_gates_column.py).
            row["gates_triggered"]    = result["gates_triggered"]
            row["modifiers_applied"]  = result["modifiers_applied"]
            # Convert floats → Decimal before DynamoDB persistence.
            row["score_breakdown"]    = self._to_dynamo(result["breakdown"])

            # Semantic fields — only persist when present (None means we
            # didn't call Haiku for this job — keeps DynamoDB rows
            # smaller and avoids confusing the frontend).
            if result.get("semantic_score") is not None:
                row["semantic_score"]     = result["semantic_score"]
                row["semantic_rationale"] = result.get("semantic_rationale") or ""
                row["semantic_scored_at"] = result.get("semantic_scored_at") or ""
                row["semantic_model"]     = result.get("semantic_model") or ""

            # work_mode is independent of semantic_score persistence because
            # we want to keep showing it even when the semantic cache expires.
            # the UI should always display remote/hybrid/onsite.
            if result.get("work_mode"):
                row["work_mode"] = result["work_mode"]

            # engagement_type — categorical chip. Always
            # populate so the frontend can filter on it; deterministic, cheap
            # (no API call), and not part of the score itself.
            if result.get("engagement_type"):
                row["engagement_type"] = result["engagement_type"]

            # Taxonomy / QoL — / R1 fields. Persist on every scrape
            # so a brand-new row immediately has chips/sort data without
            # waiting for the next nightly RescoreFn pass.
            if result.get("industries") is not None:
                row["industries"] = result.get("industries") or []
            if result.get("role_types") is not None:
                row["role_types"] = result.get("role_types") or []
            if result.get("qol_score") is not None:
                row["qol_score"] = int(result.get("qol_score") or 0)
            if result.get("qol_breakdown") is not None:
                row["qol_breakdown"] = self._to_dynamo(
                    result.get("qol_breakdown") or {}
                )
            if result.get("company_group"):
                row["company_group"] = result["company_group"]

            # Rebuild the composite sort key so ScoreIndex sorts correctly.
            from common.normalize import score_posted_sk
            row["score_posted"] = score_posted_sk(result["score"], row["posted_at"])
        except Exception as exc:
            # Log and continue — never drop a job because of a scoring failure.
            log.warn(
                "scoring_failed",
                job_id=row.get("job_id"),
                error=str(exc),
                traceback=traceback.format_exc(limit=3),
            )
        return row

    # -------- the method the worker Lambda actually calls ------------------

    def scrape_run(self) -> dict:
        """Run one full scrape cycle. Self-contained.

        Contract:
          * Per-item exceptions are caught and logged; they do NOT abort.
          * A top-level fetch() failure aborts this source only.
          * Exactly one ScrapeRuns row is written regardless of outcome.
          * All raw payloads are archived to S3 for debugging.
        Returns the ScrapeRuns summary dict.
        """
        run_started = time.monotonic()
        run_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        jobs_found = 0
        jobs_new = 0
        jobs_updated = 0
        errors: list[str] = []
        raw_payloads: list[dict] = []
        status = "ok"
        self._desc_fetches = 0   # per-run detail-fetch budget counter

        log.info("scrape_run_start", source=self.source_name)

        try:
            for payload in self.fetch():
                jobs_found += 1
                raw_payloads.append(payload)
                try:
                    raw = self.parse(payload)
                    if raw is None:
                        # Parser chose to skip — not an error.
                        continue
                    # Generic description enrichment (opt-in). Boards whose
                    # list/card payload lacks the JD set auto_fetch_description;
                    # we fetch the detail page (RawJob.url) once — JSON-LD first,
                    # then _DESC_SELECTOR — when parse() left description empty.
                    # Bounded + throttled + best-effort (failure -> stays None).
                    if (self.auto_fetch_description and not raw.description
                            and raw.url
                            and self._desc_fetches < self.max_desc_fetches):
                        self._desc_fetches += 1
                        raw.description = self._fetch_detail_description(
                            raw.url, self._DESC_SELECTOR)
                    row = self.normalize(raw)
                    # Score the job before persisting. Wrapped in try/except
                    # so a scoring bug never silently drops a job from the feed.
                    row = self._apply_score(row)
                    # put_job returns True if the row already existed.
                    existed = db.put_job(row)
                    if existed:
                        jobs_updated += 1
                    else:
                        jobs_new += 1
                except Exception as exc:
                    # One bad listing must never poison the rest.
                    errors.append(f"{type(exc).__name__}: {exc}")
                    log.warn(
                        "scrape_item_failed",
                        source=self.source_name,
                        error=str(exc),
                        traceback=traceback.format_exc(limit=3),
                    )
        except Exception as exc:
            # fetch() itself blew up. Run is over, but we still record
            # a row so health.html shows the failure.
            status = "error"
            errors.append(f"{type(exc).__name__}: {exc}")
            log.error(
                "scrape_run_failed",
                source=self.source_name,
                error=str(exc),
                traceback=traceback.format_exc(limit=5),
            )
        else:
            status = "ok" if not errors else "partial"

        # Archive raw payloads to S3 — best-effort. Failure to archive
        # shouldn't mask the real scrape outcome.
        raw_key = None
        if raw_payloads:
            try:
                raw_key = self._archive_raw(run_ts, raw_payloads)
            except Exception as exc:
                log.warn(
                    "scrape_archive_failed",
                    source=self.source_name,
                    error=str(exc),
                )

        duration_ms = int((time.monotonic() - run_started) * 1000)
        expires_at = int(
            (datetime.now(timezone.utc) + timedelta(days=_SCRAPE_RUN_TTL_DAYS))
            .timestamp()
        )

        summary = {
            "source_name": self.source_name,
            "run_timestamp": run_ts,
            "status": status,
            "jobs_found": jobs_found,
            "jobs_new": jobs_new,
            "jobs_updated": jobs_updated,
            "duration_ms": duration_ms,
            "expires_at": expires_at,
        }
        if errors:
            # Truncate so we stay well under DynamoDB's 400KB item limit.
            summary["error_message"] = " | ".join(errors)[:8000]
        if raw_key:
            summary["raw_s3_key"] = raw_key

        db.put_scrape_run(summary)
        log.info("scrape_run_done", **summary)
        return summary

    # -------- S3 archival --------------------------------------------------

    def _archive_raw(self, run_ts: str, payloads: list[dict]) -> str:
        """Write one gzipped JSONL file per run.
        Key: raw/{source}/{YYYY}/{MM}/{DD}/{run_ts}-{short_uuid}.jsonl.gz
        """
        bucket = os.environ.get("RAW_SCRAPE_BUCKET")
        if not bucket:
            raise RuntimeError("RAW_SCRAPE_BUCKET env var not set")
        if self._s3_client is None:
            self._s3_client = boto3.client("s3")

        # run_ts looks like "2026-04-16T12:00:00Z" — split into date parts.
        date_part = run_ts.split("T", 1)[0]
        y, m, d = date_part.split("-")
        key = (
            f"raw/{self.source_name}/{y}/{m}/{d}/"
            f"{run_ts}-{uuid.uuid4().hex[:8]}.jsonl.gz"
        )

        # Gzip in memory. Raw scrape volumes are small (<10MB typical).
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            for p in payloads:
                gz.write((json.dumps(p, default=str) + "\n").encode("utf-8"))

        self._s3_client.put_object(
            Bucket=bucket,
            Key=key,
            Body=buf.getvalue(),
            ContentType="application/x-ndjson",
            ContentEncoding="gzip",
        )
        return key
