"""/api/prefs — read + write the original author's UserPrefs entries.

UserPrefs is a single-user table (PK="owner", SK=<config_key>). The
frontend uses three keys today:
  hidden_companies  — list[str] of normalized company names to suppress
  saved_searches    — list[dict] of {name, query} entries
  display_options   — small dict (e.g., {"hide_below_score": 50})

GET /api/prefs        → { ok, prefs: {hidden_companies: [...], ...} }
PUT /api/prefs        → body { config_key, value } → { ok, key, value }
                        atomic upsert of one key

Auth is enforced upstream at the CloudFront edge (HTTP basic auth) so
this handler treats every request as authenticated as user "owner".
"""
import json
from typing import Any

from common import db
from common.logging import log


_USER_ID = "owner"

# Keys we know about. Others are accepted but logged at WARN so a typo
# (e.g., "savedsearch") doesn't silently create a junk row.
_KNOWN_KEYS = ("hidden_companies", "saved_searches", "display_options", "score_weights")


def _json(status_code: int, body: Any) -> dict:
    return {
        "statusCode": status_code,
        "headers": {
            "content-type": "application/json",
            "cache-control": "no-store",   # prefs change on user action
        },
        "body": json.dumps(body, default=str),
    }


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method") \
             or event.get("httpMethod") \
             or ""
    if method == "GET":
        return _get
    if method == "PUT":
        return _put(event)
    return _json(405, {"error": "method not allowed", "method": method})


def _get -> dict:
    """Return all prefs as a flat dict. Missing keys default to sensible
    empties on the client side; we return only what's actually stored."""
    prefs = db.get_prefs(_USER_ID)
    return _json(200, {"ok": True, "prefs": prefs})


def _put(event) -> dict:
    """Body must be {"config_key": str, "value": json}. The caller
    overwrites the entire row for that key — there's no merge."""
    try:
        body = json.loads(event.get("body") or "{}")
    except (ValueError, TypeError):
        return _json(400, {"error": "invalid JSON body"})

    key   = body.get("config_key")
    value = body.get("value")
    if not isinstance(key, str) or not key:
        return _json(400, {"error": "missing or invalid 'config_key'"})
    if value is None:
        return _json(400, {"error": "missing 'value' (use  or {} to clear)"})

    if key not in _KNOWN_KEYS:
        # Don't reject — the schema is opt-in — but flag it so we notice
        # typos early when a future config_key gets accidentally created.
        log.warn("prefs_unknown_key", key=key, known=_KNOWN_KEYS)

    db.put_pref(_USER_ID, key, value)
    log.info("prefs_updated", key=key)
    return _json(200, {"ok": True, "key": key, "value": value})
