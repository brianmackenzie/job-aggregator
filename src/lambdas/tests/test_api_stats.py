"""Tests for src/lambdas/api_stats.py — counts by band/track/source."""
import json

from common import db
from lambdas.api_stats import handler, _band


def _make_job(job_id, score=0, track="unscored", source=None, status="active"):
    return {
        "job_id":             job_id,
        "title":              f"Job {job_id}",
        "company":            "Acme",
        "company_normalized": "acme",
        "source":             source or job_id.split(":")[0],
        "native_id":          job_id.split(":")[1],
        "url":                f"https://example.com/{job_id}",
        "posted_at":          "2026-04-16T12:00:00Z",
        "scraped_at":         "2026-04-16T13:00:00Z",
        "status":             status,
        "track":              track,
        "score":              score,
        "score_posted":       f"{score:04d}#2026-04-16T12:00:00Z",
    }


# ----- _band ---------------------------------------------------------------

def test_band_thresholds:
    assert _band(100) == "T1"
    assert _band(78)  == "T1"
    assert _band(77)  == "T2"
    assert _band(65)  == "T2"
    assert _band(64)  == "T3"
    assert _band(50)  == "T3"
    assert _band(49)  == "below"
    assert _band(0)   == "below"


# ----- /api/stats handler --------------------------------------------------

def test_stats_empty(aws):
    resp = handler({}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["ok"] is True
    assert body["total_active"] == 0
    assert body["by_band"]   == {}
    assert body["by_track"]  == {}
    assert body["by_source"] == {}


def test_stats_groups_correctly(aws):
    db.put_job(_make_job("remoteok:1", score=85, track="gaming", source="remoteok"))
    db.put_job(_make_job("remoteok:2", score=70, track="gaming", source="remoteok"))
    db.put_job(_make_job("greenhouse:1", score=55, track="exec", source="greenhouse"))
    db.put_job(_make_job("greenhouse:2", score=20, track="other", source="greenhouse"))

    resp = handler({}, None)
    body = json.loads(resp["body"])

    assert body["total_active"] == 4
    assert body["by_band"] == {"T1": 1, "T2": 1, "T3": 1, "below": 1}
    # Tracks sort by frequency desc — gaming (2) before exec (1) before other (1).
    assert list(body["by_track"].keys)[0] == "gaming"
    assert body["by_track"]["gaming"] == 2
    assert body["by_source"]["greenhouse"] == 2
    assert body["by_source"]["remoteok"]   == 2


def test_stats_excludes_non_active(aws):
    """Only status=active jobs should be counted — saved/applied/archived
    are excluded from the dashboard headline because they're not the
    "should I look at this" universe."""
    db.put_job(_make_job("remoteok:1", score=85, status="active"))
    db.put_job(_make_job("remoteok:2", score=85, status="applied"))
    db.put_job(_make_job("remoteok:3", score=85, status="archived"))

    resp = handler({}, None)
    body = json.loads(resp["body"])

    assert body["total_active"] == 1


def test_stats_handles_non_int_score(aws):
    """Defensive: a malformed score (string, None) shouldn't crash the
    aggregation; it should fall into the "below" band."""
    job = _make_job("remoteok:1", score=0)
    job["score"] = "garbage"  # write directly with a non-int
    db.put_job(job)

    resp = handler({}, None)
    body = json.loads(resp["body"])
    assert body["total_active"] == 1
    assert body["by_band"]["below"] == 1


def test_stats_sets_short_cache_header(aws):
    """CloudFront should be allowed to cache for ~60s — verify the header
    is set so an accidental no-store regression is caught early."""
    resp = handler({}, None)
    assert "max-age" in resp["headers"]["cache-control"]
