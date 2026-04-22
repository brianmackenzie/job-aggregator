"""/api/stats — counts of active jobs by tier, track, source, score band.

Powers the dashboard's headline summary and the all.html "filters" sidebar
counts. Aggregates by paging through ScoreIndex (PK=status="active"),
which uses an INCLUDE projection of just the small fields we need
(title, company, score, track, source). Response is shape-stable so the
frontend can binding directly.

Cost: one ScoreIndex query per ~50 jobs. At ~2000 active jobs that's
~40 round-trips, each ~5ms — well under the 30s API Gateway timeout.
The endpoint is cached for 60s by CloudFront (cache-control header) so
in steady state most page loads hit the cache rather than the Lambda.
"""
import json
from collections import Counter
from typing import Any

from common import db


# Score bands used across the frontend. Mirrors the visual badge tiers
# in css/app.css (s-green / s-yellow / s-orange / s-gray) but uses the
# user-facing T-labels the original author thinks in.
def _band(score: int) -> str:
    if score >= 78: return "T1"   # high-confidence dream-tier
    if score >= 65: return "T2"   # primary-tier worth applying to
    if score >= 50: return "T3"   # worth a look
    return "below"                # noise — rarely surfaced


def _json(status: int, body: Any) -> dict:
    return {
        "statusCode": status,
        "headers": {
            "content-type": "application/json",
            # Short cache so the dashboard isn't pummeled on every reload
            # but the original author still sees fresh numbers within a minute.
            "cache-control": "public, max-age=60",
        },
        "body": json.dumps(body, default=str),
    }


def handler(event, context):
    by_band:    Counter = Counter
    by_track:   Counter = Counter
    by_source:  Counter = Counter
    total = 0

    cursor = None
    # Hard cap to defend against runaway ingestion bugs. 5000 active jobs
    # would already be 5x our normal volume; if we ever exceed it, the
    # numbers shown will be stale-but-not-crazy and the API stays fast.
    safety_cap = 5000
    pages = 0

    while True:
        items, cursor = db.query_jobs_by_score(
            status="active",
            limit=200,
            cursor=cursor,
        )
        pages += 1
        for j in items:
            total += 1
            score = j.get("score")
            try:
                score = int(score) if score is not None else 0
            except (TypeError, ValueError):
                score = 0
            by_band[_band(score)] += 1
            track = j.get("track") or "unscored"
            by_track[track] += 1
            source = j.get("source") or "unknown"
            by_source[source] += 1
            if total >= safety_cap:
                cursor = None
                break
        if not cursor:
            break
        if pages >= 50:
            # Guard rail — should never hit at normal scale.
            break

    return _json(200, {
        "ok": True,
        "total_active": total,
        "by_band":   dict(by_band),
        "by_track":  dict(by_track.most_common),
        "by_source": dict(by_source.most_common),
    })
