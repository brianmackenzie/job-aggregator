"""/api/jobs* list, fetch one, record action, plus /api/taxonomy.

Routes:
    GET  /api/jobs?<filters>          _list (legacy, score-only)
    GET  /api/jobs/browse?<filters>   _browse (R2 — multi-filter + sort)
    GET  /api/jobs/{job_id}           _get
    POST /api/jobs/{job_id}/action    _action
    POST /api/jobs/bulk_action        _bulk_action
    GET  /api/taxonomy                _taxonomy (R2 — facet labels for the UI)

R2 query params on /api/jobs/browse (all optional, all comma-separated
where multi-value):
    status         active | saved | archived | applied   (default: active)
    industries     gaming,tech                            (OR within field)
    role_types     product_strategy,strategy             (OR within field)
    company_groups tier_s,gaming_aaa                     (OR within field)
    work_modes     remote,hybrid                         (OR within field)
    min_score      0-100
    min_qol        0-100
    min_salary     dollars (e.g. 250000)
    sort_by        score | qol | comp | newest | oldest | company_asc | company_desc          (default: score)
    sort_dir       asc | desc                            (default: desc)
    limit          1-200                                  (default: 50)
    offset         0+                                     (default: 0)
"""
import json
from typing import Any, Optional

from common import db
from common.logging import log
from scoring.engagement import ENGAGEMENT_LABELS
from scoring.taxonomy import (
    INDUSTRIES_CFG, ROLE_TYPES_CFG, COMPANY_GROUPS_CFG, _tier_groups,
)


# Recognized actions and the status they map to.
_ACTION_TO_STATUS = {
    "save":    "saved",
    "skip":    "archived",
    "applied": "applied",
}


def _json(status_code: int, body: Any) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",
        },
        # default=str so any leftover Decimal/datetime serializes safely.
        "body": json.dumps(body, default=str),
    }


def handler(event, context):
    route = event.get("routeKey", "")
    if route == "GET /api/jobs":
        return _list(event)
    if route == "GET /api/jobs/browse":
        return _browse(event)
    if route == "GET /api/jobs/{job_id}":
        return _get(event)
    if route == "POST /api/jobs/{job_id}/action":
        return _action(event)
    # multi-select bulk archive/save from the main feed.
    # Path is a literal "bulk_action" suffix (not a {job_id} param) so it
    # must be checked BEFORE the {job_id}/action route would catch it
    # but Method is POST and path differs (no /action suffix), so the
    # API Gateway route key is unambiguous.
    if route == "POST /api/jobs/bulk_action":
        return _bulk_action(event)
    if route == "GET /api/taxonomy":
        return _taxonomy(event)
    return _json(404, {"error": "unknown route", "route": route})


# ---------------------------------------------------------------------
# Legacy /api/jobs — score-sorted, cursor-based.  Kept for backwards
# compat with anything still calling the old endpoint.
# ---------------------------------------------------------------------

def _list(event) -> dict:
    qs = event.get("queryStringParameters") or {}
    try:
        limit = min(int(qs.get("limit", 50)), 200)
    except ValueError:
        limit = 50
    status = qs.get("status", "active")
    cursor_raw = qs.get("cursor")
    cursor = None
    if cursor_raw:
        try:
            cursor = json.loads(cursor_raw)
        except (ValueError, TypeError):
            cursor = None

    items, next_cursor = db.query_jobs_by_score(
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return _json(200, {
        "jobs": items,
        "count": len(items),
        "next_cursor": json.dumps(next_cursor) if next_cursor else None,
    })


# ---------------------------------------------------------------------
# /api/jobs/browse — R2 multi-filter + sort endpoint, offset-paginated.
# ---------------------------------------------------------------------

def _csv(qs: dict, key: str) -> list:
    """Parse a comma-separated query-string value into a stripped list.
    Returns  for missing / empty / whitespace-only."""
    raw = (qs or {}).get(key, "") or ""
    return [v.strip for v in raw.split(",") if v.strip]


def _int(qs: dict, key: str, default: int = 0, lo: Optional[int] = None,
         hi: Optional[int] = None) -> int:
    """Safe int parser with optional clamping. Bad input → default."""
    raw = (qs or {}).get(key)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except (ValueError, TypeError):
        return default
    if lo is not None and v < lo:
        v = lo
    if hi is not None and v > hi:
        v = hi
    return v


def _browse(event) -> dict:
    qs = event.get("queryStringParameters") or {}

    # Free-text search (server-side, replaces the broken
    # client-side substring filter that only saw the loaded page).
    # Strip whitespace; treat empty string as "no search".
    q = (qs.get("q") or "").strip or None

    # Filters (all multi-value lists).
    industries       = _csv(qs, "industries")
    role_types       = _csv(qs, "role_types")
    company_groups   = _csv(qs, "company_groups")
    work_modes       = _csv(qs, "work_modes")
    # engagement_type chip filter. Values are one of:
    #   fulltime | contract | interim_fractional | advisor | unclear
    engagement_types = _csv(qs, "engagement_types")

    # Numeric filters.
    min_score  = _int(qs, "min_score",  0, 0, 100)
    min_qol    = _int(qs, "min_qol",    0, 0, 100)
    min_salary = _int(qs, "min_salary", 0, 0, 10_000_000)

    # dedup by (company, title), default ON. Pass dedup=false
    # to disable (debug / inventory views).
    dedup_raw = (qs.get("dedup") or "true").strip.lower
    dedup = dedup_raw not in ("false", "0", "no", "off")

    # Sort.
    # the "semantic" option is no longer surfaced
    # in /taxonomy (the UI dropdown dropped it) because `score` is now the
    # Haiku-blended rank for every row — the two had converged. But we
    # still *accept* sort_by=semantic from any old client / bookmark that
    # still sends it, by coercing it to `score` here. That keeps bookmarks
    # and saved-search URLs from 400-ing.
    sort_by  = (qs.get("sort_by")  or "score").lower
    sort_dir = (qs.get("sort_dir") or "desc").lower
    if sort_by == "semantic":
        sort_by = "score"
    # `company_asc` / `company_desc` group rows by company name and rank
    # score within each group. Direction lives in the sort key itself
    # (like newest/oldest), so sort_dir is ignored for these two.
    if sort_by not in {"score", "qol", "comp", "newest", "oldest",
                       "company_asc", "company_desc"}:
        sort_by = "score"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    # Status + pagination.
    status = qs.get("status", "active")
    limit  = _int(qs, "limit",  50, 1, 200)
    offset = _int(qs, "offset",  0, 0)

    result = db.query_jobs_for_browse(
        status=status,
        q=q,
        industries=industries,
        role_types=role_types,
        company_groups=company_groups,
        work_modes=work_modes,
        engagement_types=engagement_types,
        min_score=min_score,
        min_qol=min_qol,
        min_salary=min_salary,
        dedup=dedup,
        sort_by=sort_by,
        sort_dir=sort_dir,
        limit=limit,
        offset=offset,
    )

    return _json(200, {
        "jobs":        result["jobs"],
        "count":       len(result["jobs"]),
        "total":       result["total"],
        "raw_total":   result.get("raw_total", result["total"]),
        "has_more":    result["has_more"],
        "next_offset": result["next_offset"],
        # Echo the resolved query so the client can render an "active
        # filters" pill row from the server's authoritative parse.
        "query": {
            "status":           status,
            "q":                q,
            "industries":       industries,
            "role_types":       role_types,
            "company_groups":   company_groups,
            "work_modes":       work_modes,
            "engagement_types": engagement_types,
            "min_score":        min_score,
            "min_qol":          min_qol,
            "min_salary":       min_salary,
            "dedup":            dedup,
            "sort_by":          sort_by,
            "sort_dir":         sort_dir,
            "limit":            limit,
            "offset":           offset,
        },
    })


# ---------------------------------------------------------------------
# /api/taxonomy — facet labels so the UI can render filter chips
# without hardcoding the names.
# ---------------------------------------------------------------------

def _taxonomy(event) -> dict:
    """Return the facet labels for filter chips. Always 200, no filters."""
    industries = [
        {"value": k, "label": v.get("label", k)}
        for k, v in INDUSTRIES_CFG.items
    ]
    role_types = [
        {"value": k, "label": v.get("label", k)}
        for k, v in ROLE_TYPES_CFG.items
    ]
    # Tier groups are derived from companies.yaml; YAML-defined company
    # groups come second in the chip rail.
    tier_g = _tier_groups
    company_groups = 
    for tier_key, label in (("tier_s", "Tier S"), ("tier_1", "Tier 1"),
                            ("tier_2", "Tier 2")):
        if tier_g.get(tier_key):
            company_groups.append({"value": tier_key, "label": label})
    for k, v in COMPANY_GROUPS_CFG.items:
        company_groups.append({"value": k, "label": v.get("label", k)})

    return _json(200, {
        "industries":     industries,
        "role_types":     role_types,
        "company_groups": company_groups,
        "work_modes": [
            {"value": "remote",  "label": "Remote"},
            {"value": "hybrid",  "label": "Hybrid"},
            {"value": "onsite",  "label": "Onsite"},
            {"value": "unclear", "label": "Unclear"},
        ],
        # engagement type — categorical chip (replaces the old
        # 0.01-weight `engagement_type` algo signal). Order = the priority
        # in which the detector resolves a tie, which is also the order
        # most useful in the chip rail (the original author sees the rare exec-track
        # engagement options before the default Full-time).
        "engagement_types": [
            {"value": "fulltime",            "label": ENGAGEMENT_LABELS["fulltime"]},
            {"value": "interim_fractional",  "label": ENGAGEMENT_LABELS["interim_fractional"]},
            {"value": "advisor",             "label": ENGAGEMENT_LABELS["advisor"]},
            {"value": "contract",            "label": ENGAGEMENT_LABELS["contract"]},
            {"value": "unclear",             "label": ENGAGEMENT_LABELS["unclear"]},
        ],
        "sort_options": [
            # dropped the "Semantic (Haiku) only"
            # option. Once the rescore pipeline finished blending every
            # row's algo + semantic scores, `score` IS the Haiku-weighted
            # rank, so a separate option was just duplicate signal. The
            # backend still accepts `sort_by=semantic` for old clients
            # (it's coerced to `score` in the browse handler) — see
            # the sort-validation block at the top of this module.
            {"value": "score",        "label": "Best match"},
            {"value": "qol",          "label": "Quality of life"},
            {"value": "comp",         "label": "Compensation"},
            {"value": "newest",       "label": "Newest"},
            {"value": "oldest",       "label": "Oldest"},
            # Group by company, then rank score within each company.
            # Useful for scanning every active role at a target employer
            # side-by-side instead of scattered across the feed.
            {"value": "company_asc",  "label": "Company (A\u2013Z)"},
            {"value": "company_desc", "label": "Company (Z\u2013A)"},
            ],
        "statuses": [
            {"value": "active",   "label": "Active"},
            {"value": "saved",    "label": "Saved"},
            {"value": "applied",  "label": "Applied"},
            {"value": "archived", "label": "Archived"},
        ],
    })


# ---------------------------------------------------------------------
# Get one + action — unchanged from .
# ---------------------------------------------------------------------

def _get(event) -> dict:
    job_id = (event.get("pathParameters") or {}).get("job_id")
    if not job_id:
        return _json(400, {"error": "missing job_id"})
    job = db.get_job(job_id)
    if not job:
        return _json(404, {"error": "not found"})
    return _json(200, {"job": job})


def _action(event) -> dict:
    job_id = (event.get("pathParameters") or {}).get("job_id")
    if not job_id:
        return _json(400, {"error": "missing job_id"})
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid JSON body"})

    action = body.get("action")
    notes = body.get("notes")  # optional
    if action not in _ACTION_TO_STATUS:
        return _json(400, {
            "error": "invalid action",
            "valid": sorted(_ACTION_TO_STATUS.keys),
        })

    new_status = _ACTION_TO_STATUS[action]
    updated = db.update_job_action(job_id, new_status, user_notes=notes)
    if updated is None:
        return _json(404, {"error": "not found"})

    log.info("job_action", job_id=job_id, action=action, new_status=new_status)
    return _json(200, {"ok": True, "job": updated})


# ---------------------------------------------------------------------
# /api/jobs/bulk_action — multi-select.
#
# Accepts:
#   {
#     "action":  "save" | "skip" | "applied",   # same vocab as /action
#     "job_ids": ["src1:nat1", "src1:nat2", ...],
#     "notes":   "...optional shared note..."   # rare; usually omitted
#   }
#
# Returns:
#   {
#     "ok":        bool (true iff every job_id was updated),
#     "updated":   int count of rows that succeeded,
#     "missing":   list[str] of job_ids that don't exist (404'd),
#     "errors":    list[{"job_id", "error"}] for unexpected failures,
#     "new_status": "saved" | "archived" | "applied"
#   }
#
# Caps the input at 200 ids per call (matches the browse limit) so a
# runaway client can't tie up the Lambda for minutes. Cache invalidation
# happens once inside db.bulk_update_action — the caller doesn't need
# to do it.
# ---------------------------------------------------------------------

# Hard cap on per-call batch size. Picked to match the browse `limit` cap
# (also 200) — the original author can't select more rows than one page can show, so
# this is generous in practice and safe at the Lambda timeout boundary.
_BULK_ACTION_MAX = 200


def _bulk_action(event) -> dict:
    """Apply one action to many jobs in a single request."""
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid JSON body"})

    action = body.get("action")
    if action not in _ACTION_TO_STATUS:
        return _json(400, {
            "error": "invalid action",
            "valid": sorted(_ACTION_TO_STATUS.keys),
        })

    raw_ids = body.get("job_ids") or 
    if not isinstance(raw_ids, list):
        return _json(400, {"error": "job_ids must be a list"})

    # De-dupe + drop blanks/non-strings up front; cap at the per-call max.
    seen: set = set
    job_ids: list = 
    for jid in raw_ids:
        if not isinstance(jid, str):
            continue
        jid = jid.strip
        if not jid or jid in seen:
            continue
        seen.add(jid)
        job_ids.append(jid)

    if not job_ids:
        return _json(400, {"error": "job_ids is empty"})
    if len(job_ids) > _BULK_ACTION_MAX:
        return _json(400, {
            "error": "too many job_ids",
            "max": _BULK_ACTION_MAX,
            "got": len(job_ids),
        })

    notes = body.get("notes")  # optional shared note across all rows
    new_status = _ACTION_TO_STATUS[action]

    result = db.bulk_update_action(job_ids, new_status, user_notes=notes)

    log.info(
        "bulk_job_action",
        action=action,
        new_status=new_status,
        requested=len(job_ids),
        updated=len(result["ok"]),
        missing=len(result["missing"]),
        errors=len(result["errors"]),
    )

    return _json(200, {
        "ok":         (len(result["missing"]) == 0
                       and len(result["errors"]) == 0),
        "updated":    len(result["ok"]),
        "missing":    result["missing"],
        "errors":     result["errors"],
        "new_status": new_status,
    })
