"""DynamoDB accessors. All table names come from env vars (set by SAM at
deploy time, overridden by conftest.py during tests).

At the DB boundary we deal in plain dicts — NOT the dataclasses in
models.py. That keeps Lambda handlers JSON-serializable and avoids a
layer of to/from_dict conversions on every hot path.

Two small gotchas we solve here:
  1. DynamoDB rejects Python floats (precision); `_encode` converts
     floats to Decimal before write.
  2. DynamoDB returns numbers as Decimal; `_decode` coerces Decimal
     back to int or float before return so JSON responses "just work".
"""
import os
from decimal import Decimal
from typing import Any, Iterable, Optional

import boto3
from boto3.dynamodb.conditions import Key


# ------------------------------------------------------------------
# Lazy boto3 resource. Cached in module-level var for warm-Lambda perf;
# tests reset _resource in conftest so each mock_aws context gets a
# fresh client.
# ------------------------------------------------------------------
_resource = None


def _ddb:
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb")
    return _resource


def _table(env_var: str):
    name = os.environ[env_var]
    return _ddb.Table(name)


def jobs_table:          return _table("JOBS_TABLE")
def companies_table:     return _table("COMPANIES_TABLE")
def scrape_runs_table:   return _table("SCRAPE_RUNS_TABLE")
def user_prefs_table:    return _table("USER_PREFS_TABLE")


# ------------------------------------------------------------------
# Decimal <-> native coercion. Applied on every read and write so the
# rest of the codebase never has to think about Decimal.
# ------------------------------------------------------------------

def _encode(value: Any) -> Any:
    """Recursively convert floats -> Decimal for DynamoDB.
    Leaves ints, strs, bools, None, etc. untouched."""
    if isinstance(value, float):
        # str round-trips through Decimal without IEEE-754 surprises.
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    return value


def _decode(value: Any) -> Any:
    """Recursively convert DynamoDB Decimal -> int or float."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value else float(value)
    if isinstance(value, dict):
        return {k: _decode(v) for k, v in value.items}
    if isinstance(value, (list, tuple)):
        return [_decode(v) for v in value]
    if isinstance(value, set):
        return {_decode(v) for v in value}
    return value


# ------------------------------------------------------------------
# Jobs
# ------------------------------------------------------------------

# Attributes representing user state — must NOT be overwritten by a
# scraper re-ingesting the same job_id. If the user marks a job
# "applied" on Monday, Tuesday's re-scrape must preserve that.
_PRESERVE_ON_UPSERT = ("status", "user_notes")


def put_job(job: dict) -> bool:
    """Upsert a job row. Returns True if the row existed (updated),
    False if newly inserted. Preserves user-modified fields on update."""
    table = jobs_table
    existing = get_job(job["job_id"])
    if existing:
        for field_name in _PRESERVE_ON_UPSERT:
            if field_name in existing:
                job[field_name] = existing[field_name]
    table.put_item(Item=_encode(job))
    return existing is not None


def update_job_action(
    job_id: str,
    status: str,
    user_notes: Optional[str] = None,
) -> Optional[dict]:
    """Set user-state fields on a job. Bypasses put_job's preserve logic
    (which would clobber the new status with the old one).

    Also rewrites score_posted because status is the GSI partition key on
    ScoreIndex — the row needs to land in the new partition immediately
    so the dashboard's "active" feed reflects the action. Returns the
    updated row, or None if the job doesn't exist.
    """
    table = jobs_table
    existing = get_job(job_id)
    if not existing:
        return None

    update_expr = ["#s = :s"]
    values: dict[str, Any] = {":s": status}
    names = {"#s": "status"}
    if user_notes is not None:
        update_expr.append("user_notes = :n")
        values[":n"] = user_notes

    resp = table.update_item(
        Key={"job_id": job_id},
        UpdateExpression="SET " + ", ".join(update_expr),
        ExpressionAttributeValues=_encode(values),
        ExpressionAttributeNames=names,
        ReturnValues="ALL_NEW",
    )
    return _decode(resp.get("Attributes"))


def get_job(job_id: str) -> Optional[dict]:
    resp = jobs_table.get_item(Key={"job_id": job_id})
    item = resp.get("Item")
    return _decode(item) if item else None


def query_jobs_by_score(
    status: str = "active",
    limit: int = 50,
    cursor: Optional[dict] = None,
) -> tuple[list[dict], Optional[dict]]:
    """Newest + highest-scoring jobs first — the dashboard feed.

    Returns (items, next_cursor). Pass next_cursor back as `cursor`
    to paginate. ScanIndexForward=False gets descending SK order,
    which (thanks to score_posted_sk's zero-padding) means highest
    score first, then most recently posted.

    after the GSI query, we hydrate `work_mode` from
    the main table via a single BatchGetItem so the list UI can show
    remote/hybrid/onsite chips without the (disruptive) cost of a GSI
    projection schema change.
    """
    kwargs = {
        "IndexName": "ScoreIndex",
        "KeyConditionExpression": Key("status").eq(status),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    resp = jobs_table.query(**kwargs)
    items = _decode(resp.get("Items", ))

    # Hydrate work_mode for each row from the main table. Single round-trip
    # batch_get_item, up to 100 keys per call (we only ever have `limit`
    # rows here, so one call suffices). Fields already on the GSI projection
    # (title, company, url, score, posted_at, track) are left untouched.
    _hydrate_work_mode(items)

    return items, resp.get("LastEvaluatedKey")


def _hydrate_work_mode(items: list[dict]) -> None:
    """Add `work_mode` and `engagement_type` (if stored) to each item in-place.

    items comes back from a GSI query with a restricted INCLUDE projection;
    work_mode + engagement_type live on the main table row only. One
    BatchGetItem call fetches them all in a single round-trip. Silent on
    failure — missing fields just render as empty chips in the UI.

    also hydrates `engagement_type` so the new chip
    rail can render Full-time / Contract / Interim-Fractional / Advisor
    labels on the legacy /api/jobs response (used by the old `index.html`
    flow). The new /api/jobs/browse endpoint reads from the full row scan
    so doesn't need this hydration.
    """
    if not items:
        return
    try:
        keys = [{"job_id": it["job_id"]} for it in items if it.get("job_id")]
        if not keys:
            return
        table_name = os.environ["JOBS_TABLE"]
        resp = _ddb.batch_get_item(
            RequestItems={
                table_name: {
                    "Keys": keys,
                    "ProjectionExpression": "job_id, work_mode, engagement_type",
                }
            }
        )
        fetched = resp.get("Responses", {}).get(table_name, ) or 
        # Index by job_id once, then merge both fields onto each item below.
        by_id = {r["job_id"]: r for r in fetched}
        for it in items:
            row = by_id.get(it.get("job_id")) or {}
            wm = row.get("work_mode")
            if wm:
                it["work_mode"] = wm
            et = row.get("engagement_type")
            if et:
                it["engagement_type"] = et
    except Exception:
        # Never fail the list call because hydration broke.
        pass


# ----------------------------------------------------------------------
# Browse-scan cache. The /api/jobs/browse endpoint full-scans the Jobs
# table on every request, which at 10k+ rows costs ~1-2s of Lambda time
# and a full RCU pass. Almost all reads share the same status (active),
# so we cache the post-scan items list per status for 60s in the warm
# Lambda container. Repeat hits inside the TTL replay from RAM in <50ms.
#
# Correctness model: cache is per-Lambda-container (not shared); a stale
# entry is at most 60s out of date. Worst case: a job is scored at T+0,
# the original author refreshes at T+1 and sees the old version for 59s. For a personal
# dashboard that's fine. If we ever need stronger freshness we can:
#   1) Drop the TTL.
#   2) Bust the cache from rescore.py / scrape_worker.py via SNS or a
#      version key on a small DDB row.
# ----------------------------------------------------------------------

import time as _time

_BROWSE_CACHE: dict = {}                # status -> (expires_at_epoch, items)
# bumped from 60s → 600s. Rationale: the warm path is ~30ms
# but a cache miss costs ~7s (full 10K-row scan + dedup + projection). the original author
# is a single user — a 10-min staleness window is fine, and it slashes the
# odds of a browser session ever paying the 7s penalty mid-browse. The
# RescoreFn / ScrapeWorker handlers explicitly call invalidate_browse_cache
# after writes, so user-visible changes still propagate within seconds inside
# the same Lambda container.
_BROWSE_CACHE_TTL_SECONDS = 600


def _projection_for_browse -> tuple[str, dict]:
    """Project only the fields the browse list view actually renders.

    Drops the heavy blobs we used to ship on every list page:
      - description       (multi-KB HTML/plain text)
      - score_breakdown   (nested dict, ~30 numeric subscores)
      - qol_breakdown     (nested numeric subscores)
      - raw / payload     (anything we stored from the source)

    For 10k rows × ~5KB description × default page = ~50MB savings on a
    cold load.  the original author's browser only needs the trimmed schema; the full
    row is fetched on-demand via /api/jobs/{job_id} when he opens a card.

    added `semantic_score` and `semantic_rationale`
    so the card view can show a one-line LLM verdict snippet without the
    user having to drill into the detail page. Rationale is truncated
    server-side (see `_RATIONALE_SNIPPET_CHARS` in query_jobs_for_browse)
    before serialization, so the wire payload stays small even though
    the cache holds the full string.

    Returns (ProjectionExpression, ExpressionAttributeNames). We use
    aliases for the few words DynamoDB reserves (`status`, `source`,
    `location`).
    """
    fields = [
        "job_id", "title", "company", "company_normalized", "company_group",
        "company_tier", "score", "qol_score", "track", "engagement_type",
        "work_mode", "industries", "role_types", "salary_min", "salary_max",
        "posted_at",
        # semantic snippet on cards — the rationale can be 1-2KB
        # so we project it but truncate before send. The cache holds full
        # text so the per-job detail view still renders the full prose.
        "semantic_score", "semantic_rationale",
        # Aliased reserved words below — DynamoDB rejects bare `url`,
        # `status`, `source`, `location` in a ProjectionExpression.
        "#s", "#src", "#loc", "#url",
    ]
    names = {
        "#s":   "status",
        "#src": "source",
        "#loc": "location",
        "#url": "url",
    }
    return ",".join(fields), names


# how many chars of the semantic rationale to ship on the card
# list response. The full text lives on the row (and in the warm cache)
# and is fetched intact on the detail page. 220 chars ≈ ~2 short
# sentences — enough to convey the verdict without dominating the card.
_RATIONALE_SNIPPET_CHARS = 220


def _snippet_rationale(text: object) -> str:
    """Truncate a semantic rationale to the configured snippet length.

    - Non-strings (None, numbers from a quirky row) become empty string.
    - Strings ≤ limit pass through untouched.
    - Longer strings get cut at the nearest space before the limit so we
      don't slice mid-word, then a single ellipsis is appended.
    """
    if not isinstance(text, str) or not text:
        return ""
    if len(text) <= _RATIONALE_SNIPPET_CHARS:
        return text
    cut = text.rfind(" ", 0, _RATIONALE_SNIPPET_CHARS)
    if cut < _RATIONALE_SNIPPET_CHARS // 2:
        # No good space break in the back half — just hard-cut.
        cut = _RATIONALE_SNIPPET_CHARS
    return text[:cut].rstrip + "\u2026"   # … (single ellipsis char)


# statuses we deliberately skip the warm cache for.
# the original author's UX never re-browses the archived bucket back-to-back the way he
# does the active feed, so paying the 7s scan once when he visits the
# archived tab beats burning 10MB of Lambda memory on a list he won't
# look at twice in 10 minutes. Saved + applied + active stay cached
# (frequent re-browse, smaller cohorts).
_UNCACHED_STATUSES = frozenset({"archived"})


def _scan_active_items(status: str) -> list[dict]:
    """Scan + (optionally) cache the items list for one status.

    Pulls from _BROWSE_CACHE when warm and inside TTL, otherwise scans
    the table (with the trimmed projection) and refills the cache.

    `archived` (and any other status in `_UNCACHED_STATUSES`) bypasses
    the cache entirely — always a fresh scan, never stored. Rationale:
    the archived bucket is large but rarely visited, and the user
    explicitly requested we not cache it.
    """
    now = _time.time
    use_cache = status not in _UNCACHED_STATUSES
    if use_cache:
        cached = _BROWSE_CACHE.get(status)
        if cached and cached[0] > now:
            return cached[1]

    table = jobs_table
    proj_expr, proj_names = _projection_for_browse
    # `status` is a DynamoDB reserved word so we alias it via #s — and
    # we already need the alias in the projection, so we reuse the
    # same ExpressionAttributeNames map to avoid a duplicate-key error.
    scan_kwargs: dict = {
        "FilterExpression": "#s = :s",
        "ProjectionExpression": proj_expr,
        "ExpressionAttributeNames": proj_names,
        "ExpressionAttributeValues": {":s": status},
    }

    items: list[dict] = 
    last = None
    while True:
        if last:
            scan_kwargs["ExclusiveStartKey"] = last
        resp = table.scan(**scan_kwargs)
        items.extend(_decode(resp.get("Items", )))
        last = resp.get("LastEvaluatedKey")
        if not last:
            break

    if use_cache:
        _BROWSE_CACHE[status] = (now + _BROWSE_CACHE_TTL_SECONDS, items)
    return items


# bulk-action helper for the multi-select UI.
# update_job_action does a single-row update with ReturnValues="ALL_NEW"
# (round-trip per row + full row return). When the original author taps "Archive
# selected" on 30 cards, that's 30 sequential Dynamo updates + 30 cache
# misses on the next /browse fetch.  This helper:
#   1. Fans out the updates concurrently within the Lambda (boto3 is
#      thread-safe and DDB is high-throughput on writes).
#   2. Busts the browse cache once at the end, not per-row.
#   3. Returns a {ok, missing, errors} summary instead of N row payloads.
def bulk_update_action(
    job_ids: list[str],
    status: str,
    user_notes: Optional[str] = None,
) -> dict:
    """Apply `status` (and optional `user_notes`) to many jobs at once.

    Returns a dict:
      {
        "ok":      list[str] of job_ids successfully updated,
        "missing": list[str] of job_ids that don't exist,
        "errors":  list[{"job_id": str, "error": str}],
      }

    All work is wrapped in try/except per-row so one bad job_id never
    aborts the rest. Cache invalidation runs once at the end so the
    /browse endpoint sees fresh state on the very next call.

    Note: we use plain UpdateItem with ConditionExpression to detect
    missing rows cheaply, instead of GetItem-then-UpdateItem (which
    would double the Dynamo cost and create a TOCTOU race).
    """
    if not job_ids:
        return {"ok": , "missing": , "errors": }

    table = jobs_table
    ok: list[str] = 
    missing: list[str] = 
    errors: list[dict] = 

    # Build update once; reuse for every row.
    update_expr_parts = ["#s = :s"]
    values: dict[str, Any] = {":s": status}
    names = {"#s": "status"}
    if user_notes is not None:
        update_expr_parts.append("user_notes = :n")
        values[":n"] = user_notes
    update_expr = "SET " + ", ".join(update_expr_parts)

    # ConditionExpression: row must already exist (job_id is the PK).
    # If it doesn't, DynamoDB raises ConditionalCheckFailedException
    # which we catch and bucket into `missing`.
    from botocore.exceptions import ClientError

    for jid in job_ids:
        try:
            table.update_item(
                Key={"job_id": jid},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=_encode(values),
                ExpressionAttributeNames=names,
                ConditionExpression="attribute_exists(job_id)",
            )
            ok.append(jid)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ConditionalCheckFailedException":
                missing.append(jid)
            else:
                errors.append({"job_id": jid, "error": code or str(exc)})
        except Exception as exc:
            # Any other class of failure (network, permission) — record
            # and continue. Caller logs the summary; one bad row never
            # blocks the rest.
            errors.append({"job_id": jid, "error": str(exc)})

    # Bust the cache once — affects this Lambda container only, but
    # ApiJobsFn handles the very next /browse request from the same
    # container in the warm-path scenario, so the user sees fresh data
    # on reload. The 600s TTL eventually catches stragglers.
    invalidate_browse_cache

    return {"ok": ok, "missing": missing, "errors": errors}


def invalidate_browse_cache(status: Optional[str] = None) -> None:
    """Clear the browse cache. Call after writes if you need fresh reads.

    Optional `status` clears just one partition; default clears everything.
    Used by rescore.py at the end of a full pass + by tests.
    """
    if status is None:
        _BROWSE_CACHE.clear
    else:
        _BROWSE_CACHE.pop(status, None)


def _with_rationale_snippet(job: dict) -> dict:
    """Return a shallow copy of `job` with `semantic_rationale` truncated.

    Used by query_jobs_for_browse on the page slice so the wire payload
    stays small. We must not mutate the input — it lives in the warm
    cache and other callers (different offsets, different filters that
    happen to land the same row in their page) need the full text.
    """
    if not job:
        return job
    rationale = job.get("semantic_rationale")
    if not rationale:
        return job
    snippet = _snippet_rationale(rationale)
    if snippet == rationale:
        return job
    out = dict(job)
    out["semantic_rationale"] = snippet
    return out


def _normalize_title(title: str) -> str:
    """Lower + collapse whitespace for dedup grouping. Intentionally light
    — over-aggressive normalization (stripping "Senior", "II", parens) risks
    collapsing genuinely different roles. Start conservative; tighten only
    if the original author sees obvious near-duplicates surviving."""
    if not title:
        return ""
    return " ".join(title.lower.split)


def query_jobs_for_browse(
    status: str = "active",
    q: Optional[str] = None,
    industries: Optional[list] = None,
    role_types: Optional[list] = None,
    company_groups: Optional[list] = None,
    work_modes: Optional[list] = None,
    engagement_types: Optional[list] = None,
    min_score: int = 0,
    min_qol: int = 0,
    min_salary: int = 0,
    dedup: bool = True,
    sort_by: str = "score",
    sort_dir: str = "desc",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Browse query for the redesigned UI (R2).

    Filters are AND across categories, OR within a category:
      industries=["gaming","tech"] role_types=["strategy"]
      → (gaming OR tech) AND strategy

    Search (`q`): substring match across title + company + location. Case-
    insensitive. Description is NOT in the search corpus because we no
    longer load it on the browse path (perf), but title+company+location
    catches the typical search cases (company name, role keyword, city).
    Per-job description search still works on /api/jobs/{id}.

    Dedup (`dedup=True`, default): collapses (company_normalized,
    normalized_title) groups to the highest-scoring row in the group.
    The surviving row gets:
      - dupe_count    : int, 1 if no dupes, N otherwise
      - dupe_sources  : list[str] of all source strings in the group
                        (e.g. ["greenhouse", "apify_linkedin"])
    Pass `dedup=False` to see every row (debug / when the original author wants the
    underlying inventory). The dupe count is computed BEFORE pagination
    so the `total` reflects deduped roles, not raw rows.

    Sort options:
      score   — final blended score (default), descending
      qol     — qol_score, descending
      comp    — salary_min when present (rows w/o salary sink to bottom)
      newest  — posted_at descending
      oldest  — posted_at ascending

    Returns:
      {
        "jobs":         list[dict] page slice,
        "total":        int matched after filtering + dedup,
        "raw_total":    int matched after filtering, before dedup,
        "has_more":     bool,
        "next_offset":  int | None,
      }

    Implementation: still a full scan, BUT:
      - 60s warm-Lambda cache means most repeat hits skip the scan
      - tighter ProjectionExpression cuts payload ~5-10x (no descriptions)
    These two together get us out of the "1-2s per page-load" regime and
    into "instant on warm, ~500ms on cold". Hot-path GSI swap deferred —
    would need a GSI projection schema change, flagged disruptive in
    CLAUDE.md.
    """
    q                = (q or "").strip.lower or None
    industries       = [i.lower for i in (industries or ) if i]
    role_types       = [r.lower for r in (role_types or ) if r]
    company_groups   = [c.lower for c in (company_groups or ) if c]
    work_modes       = [w.lower for w in (work_modes or ) if w]
    engagement_types = [e.lower for e in (engagement_types or ) if e]

    # Cached scan. Pulls all rows for `status` once per warm Lambda per 60s.
    items = _scan_active_items(status)

    # ---- In-memory filtering ----
    def _matches(j: dict) -> bool:
        # Free-text search first: cheapest reject for the typical case
        # where the original author typed something specific.
        if q:
            hay = (
                str(j.get("title") or "") + " " +
                str(j.get("company") or "") + " " +
                str(j.get("location") or "")
            ).lower
            if q not in hay:
                return False
        if industries:
            j_inds = {str(i).lower for i in (j.get("industries") or )}
            if not (j_inds & set(industries)):
                return False
        if role_types:
            j_rts = {str(r).lower for r in (j.get("role_types") or )}
            if not (j_rts & set(role_types)):
                return False
        if company_groups:
            cg = str(j.get("company_group") or "").lower
            if cg not in set(company_groups):
                return False
        if work_modes:
            wm = str(j.get("work_mode") or "").lower
            if wm not in set(work_modes):
                return False
        if engagement_types:
            # engagement_type is a single string per job. Old
            # rows that haven't been rescored yet will be missing the
            # field entirely — treat those as "unclear" so the chip stays
            # honest rather than silently filtering them out.
            et = str(j.get("engagement_type") or "unclear").lower
            if et not in set(engagement_types):
                return False
        if min_score and (int(j.get("score") or 0) < min_score):
            return False
        if min_qol and (int(j.get("qol_score") or 0) < min_qol):
            return False
        if min_salary:
            sm = j.get("salary_min") or j.get("salary_max") or 0
            try:
                if int(sm) < min_salary:
                    return False
            except (TypeError, ValueError):
                return False
        return True

    matched = [j for j in items if _matches(j)]
    raw_total = len(matched)

    # ---- Dedup pass ----
    # Group by (company_normalized, normalized_title). Within each group,
    # keep the row with the highest score; attach `dupe_count` and the
    # full set of sources we've seen for that role.  Items missing
    # company_normalized OR title are passed through untouched (we don't
    # have a stable key to dedup on).
    if dedup and matched:
        groups: dict[tuple[str, str], dict] = {}
        passthrough: list[dict] = 
        for j in matched:
            cn = str(j.get("company_normalized") or "").strip.lower
            tn = _normalize_title(str(j.get("title") or ""))
            if not cn or not tn:
                passthrough.append(j)
                continue
            key = (cn, tn)
            existing = groups.get(key)
            if existing is None:
                # First time seeing this role — seed the group.
                j_copy = dict(j)
                j_copy["dupe_count"] = 1
                src = j.get("source")
                j_copy["dupe_sources"] = [src] if src else 
                groups[key] = j_copy
                continue
            # Merge: bump count, append source, swap winner if this row
            # has a higher score (stable behavior on ties — first wins).
            existing["dupe_count"] = int(existing.get("dupe_count", 1)) + 1
            src = j.get("source")
            if src and src not in existing["dupe_sources"]:
                existing["dupe_sources"].append(src)
            this_score = int(j.get("score") or 0)
            best_score = int(existing.get("score") or 0)
            if this_score > best_score:
                # Carry the dupe metadata into the new winner.
                carry_count   = existing["dupe_count"]
                carry_sources = existing["dupe_sources"]
                replacement = dict(j)
                replacement["dupe_count"]   = carry_count
                replacement["dupe_sources"] = carry_sources
                groups[key] = replacement
        matched = list(groups.values) + passthrough

    # ---- Sorting ----
    reverse = (sort_dir or "desc").lower != "asc"

    def _sort_key(j: dict):
        if sort_by == "qol":
            return int(j.get("qol_score") or 0)
        if sort_by == "comp":
            # Sort by salary_min (or max as fallback). Rows without
            # comp data sink to the bottom on either direction by
            # using -1 as a sentinel that's smaller than any real comp.
            sm = j.get("salary_min") or j.get("salary_max") or -1
            try:
                return int(sm)
            except (TypeError, ValueError):
                return -1
        if sort_by in ("newest", "oldest"):
            # Compare ISO strings — they're lexically sortable.
            return j.get("posted_at") or ""
        if sort_by == "semantic":
            # rank by Haiku's semantic_score directly.
            # Rows without a semantic score (prefilter rejects or not-yet-
            # rescored) get -1 so they sink to the bottom regardless of
            # direction. That matches the original author's stated intent - "use the
            # Haiku ranking while the rest of the pipeline catches up."
            ss = j.get("semantic_score")
            if ss is None:
                return -1
            try:
                return int(ss)
            except (TypeError, ValueError):
                return -1
        # default: score (the blended / final score).
        return int(j.get("score") or 0)

    matched.sort(key=_sort_key, reverse=reverse if sort_by != "oldest" else False)
    if sort_by == "oldest":
        # `oldest` is just newest-ascending; re-sort to ignore reverse arg.
        matched.sort(key=_sort_key, reverse=False)

    total = len(matched)
    page  = matched[offset : offset + limit]
    has_more = (offset + limit) < total

    # truncate semantic_rationale on the page slice
    # only. We mutate copies (not the cached items) so a subsequent call
    # at a different offset still sees the full text in the cache.
    page = [_with_rationale_snippet(j) for j in page]

    return {
        "jobs":        page,
        "total":       total,
        # Pre-dedup count so the UI can render "1,234 roles (1,516 raw)"
        # if the original author wants to see how much dedup is collapsing.
        "raw_total":   raw_total,
        "has_more":    has_more,
        "next_offset": (offset + limit) if has_more else None,
    }


def query_jobs_by_company(company_normalized: str, limit: int = 100) -> list[dict]:
    resp = jobs_table.query(
        IndexName="CompanyIndex",
        KeyConditionExpression=Key("company_normalized").eq(company_normalized),
        ScanIndexForward=False,
        Limit=limit,
    )
    return _decode(resp.get("Items", ))


def query_jobs_by_track(
    track: str,
    limit: int = 50,
    cursor: Optional[dict] = None,
) -> tuple[list[dict], Optional[dict]]:
    kwargs = {
        "IndexName": "TrackIndex",
        "KeyConditionExpression": Key("track").eq(track),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if cursor:
        kwargs["ExclusiveStartKey"] = cursor
    resp = jobs_table.query(**kwargs)
    return _decode(resp.get("Items", )), resp.get("LastEvaluatedKey")


def iter_active_jobs(batch_size: int = 100) -> Iterable[dict]:
    """Yield every active job. Used by the rescore Lambda to rewrite
    scores after the weights in config/scoring.yaml change."""
    last = None
    while True:
        kwargs = {
            "IndexName": "ScoreIndex",
            "KeyConditionExpression": Key("status").eq("active"),
            "Limit": batch_size,
        }
        if last:
            kwargs["ExclusiveStartKey"] = last
        resp = jobs_table.query(**kwargs)
        for item in resp.get("Items", ):
            yield _decode(item)
        last = resp.get("LastEvaluatedKey")
        if not last:
            break


# ------------------------------------------------------------------
# ScrapeRuns
# ------------------------------------------------------------------

def put_scrape_run(row: dict) -> None:
    scrape_runs_table.put_item(Item=_encode(row))


def get_recent_scrape_runs(source_name: str, limit: int = 20) -> list[dict]:
    """Return the most recent scrape runs for one source."""
    resp = scrape_runs_table.query(
        KeyConditionExpression=Key("source_name").eq(source_name),
        ScanIndexForward=False,
        Limit=limit,
    )
    return _decode(resp.get("Items", ))


# ------------------------------------------------------------------
# Companies
# ------------------------------------------------------------------

def upsert_company(row: dict) -> None:
    companies_table.put_item(Item=_encode(row))


def get_company(name_normalized: str) -> Optional[dict]:
    resp = companies_table.get_item(
        Key={"company_name_normalized": name_normalized}
    )
    item = resp.get("Item")
    return _decode(item) if item else None


def list_companies_by_tier(tier: str) -> list[dict]:
    resp = companies_table.query(
        IndexName="TierIndex",
        KeyConditionExpression=Key("tier").eq(tier),
    )
    return _decode(resp.get("Items", ))


# ------------------------------------------------------------------
# UserPrefs
# ------------------------------------------------------------------

def get_prefs(user_id: str = "owner") -> dict:
    """Return a dict mapping config_key -> value for this user.
    Missing user returns an empty dict."""
    resp = user_prefs_table.query(
        KeyConditionExpression=Key("user_id").eq(user_id),
    )
    out = {}
    for item in _decode(resp.get("Items", )):
        out[item["config_key"]] = item.get("value")
    return out


def put_pref(user_id: str, config_key: str, value: Any) -> None:
    user_prefs_table.put_item(Item=_encode({
        "user_id": user_id,
        "config_key": config_key,
        "value": value,
    }))
