"""Tests for src/lambdas/rescore.py.

Covers the retro fix: when score_combined returns
`semantic_api_failed=True` (Haiku 429'd / timed out / returned junk),
the per-item update_item must be SKIPPED so the previously-good blended
score+rationale is preserved on the row. Without this guard, a degraded
algo-only score gets written over a previously-good blended score,
which is exactly the inconsistency we had to clean up after the
parallel rescore tripped Anthropic's 450K-tpm rate limit.

Other behaviors covered:
  * happy path — score_combined returns a real semantic result and
    update_item is called with all the score fields.
  * skip path — when a row is too fresh (per min_age_hours), the row
    is skipped without even invoking score_combined.
"""
from __future__ import annotations

from unittest.mock import patch

from common import db
from lambdas import rescore


def _make_job(job_id: str, **overrides) -> dict:
    """Build a minimal Jobs row with the fields rescore.py reads/writes."""
    base = {
        "job_id":             job_id,
        "title":              f"Test job {job_id}",
        "company":            "Acme",
        "company_normalized": "acme",
        "source":             job_id.split(":")[0],
        "native_id":          job_id.split(":")[1],
        "url":                f"https://example.com/{job_id}",
        "posted_at":          "2026-04-15T12:00:00Z",
        "scraped_at":         "2026-04-16T12:00:00Z",
        "status":             "active",
        "track":              "exec_target",
        "score":              82,
        "algo_score":         50,
        "score_posted":       "0082#2026-04-15T12:00:00Z",
        # Pre-existing semantic data — this is what we MUST NOT clobber
        # when the next rescore's Haiku call fails.
        "semantic_score":     90,
        "semantic_rationale": "Previously-good rationale.",
        "semantic_scored_at": "2026-04-17T00:00:00Z",
        "semantic_model":     "claude-haiku-4-5-20251001",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 429-fallback bug fix — the row MUST NOT be touched when Haiku failed.
# ---------------------------------------------------------------------------

def test_api_failure_skips_update_and_increments_counter(aws):
    """When score_combined returns semantic_api_failed=True, the row is
    untouched and the api_failed_skipped counter ticks up."""
    db.put_job(_make_job("test:apifail"))

    # Mock score_combined to simulate a Haiku 429 fallback: algo-only
    # result with the new failure flag set.
    fake_result = {
        "score":              50,            # algo-only (degraded vs the cached 82)
        "tier":               "T4",
        "track":              "exec_target",
        "breakdown":          {"function": 5},
        "gates_triggered":    ,
        "modifiers_applied":  ,
        "algo_score":         50,
        "semantic_score":     None,
        "semantic_rationale": None,
        "semantic_scored_at": None,
        "semantic_model":     None,
        "semantic_skipped":   True,
        "semantic_api_failed": True,         # <- the new flag
        "work_mode":          None,
    }

    with patch("lambdas.rescore.score_combined", return_value=fake_result):
        result = rescore.handler({}, None)

    assert result["ok"] is True
    assert result["total"] == 1
    # Critical assertion — the row was NOT updated.
    assert result["updated"] == 0
    assert result["api_failed_skipped"] == 1

    # Verify the cached semantic data is still intact in DynamoDB.
    row = db.get_job("test:apifail")
    assert row["score"] == 82                         # unchanged
    assert row["semantic_score"] == 90                # unchanged
    assert row["semantic_rationale"] == "Previously-good rationale."


def test_api_failure_does_not_abort_batch(aws):
    """One 429-victim row in the middle must not stop later rows from being
    updated. Combined with above, this proves the `continue` is the right
    control-flow primitive (vs raise / break)."""
    db.put_job(_make_job("test:row-a"))
    db.put_job(_make_job("test:row-b"))
    db.put_job(_make_job("test:row-c"))

    # Build per-job results: row-a happy, row-b 429-failed, row-c happy.
    def _by_job(job, prefs, *, force_semantic=False, skip_semantic=False):
        jid = job["job_id"]
        if jid == "test:row-b":
            return {
                "score": 50, "tier": "T4", "track": "exec_target",
                "breakdown": {}, "gates_triggered": , "modifiers_applied": ,
                "algo_score": 50,
                "semantic_score": None, "semantic_rationale": None,
                "semantic_scored_at": None, "semantic_model": None,
                "semantic_skipped": True, "semantic_api_failed": True,
                "work_mode": None,
            }
        return {
            "score": 88, "tier": "T2", "track": "exec_target",
            "breakdown": {"function": 9}, "gates_triggered": ,
            "modifiers_applied": , "algo_score": 70,
            "semantic_score": 100, "semantic_rationale": "Great fit.",
            "semantic_scored_at": "2026-04-18T00:00:00Z",
            "semantic_model": "claude-haiku-test",
            "semantic_skipped": False, "semantic_api_failed": False,
            "work_mode": "remote",
        }

    with patch("lambdas.rescore.score_combined", side_effect=_by_job):
        result = rescore.handler({}, None)

    assert result["total"] == 3
    assert result["updated"] == 2                     # a + c
    assert result["api_failed_skipped"] == 1          # b
    assert result["semantic_calls"] == 2              # a + c each made a call


# ---------------------------------------------------------------------------
# Happy path — make sure the normal path still writes the row.
# ---------------------------------------------------------------------------

def test_happy_path_writes_blended_score(aws):
    """Sanity: a successful score_combined result lands in DynamoDB with
    the new blended score AND fresh semantic fields."""
    db.put_job(_make_job("test:happy",
                         score=20, semantic_score=None,
                         semantic_rationale=None, semantic_scored_at=None,
                         semantic_model=None))

    fake_result = {
        "score":              88,
        "tier":               "T2",
        "track":              "exec_target",
        "breakdown":          {"function": 10, "industry": 8},
        "gates_triggered":    ,
        "modifiers_applied":  ,
        "algo_score":         70,
        "semantic_score":     100,
        "semantic_rationale": "Strong VP target.",
        "semantic_scored_at": "2026-04-18T01:00:00Z",
        "semantic_model":     "claude-haiku-test",
        "semantic_skipped":   False,
        "semantic_api_failed": False,
        "work_mode":          "remote",
    }

    with patch("lambdas.rescore.score_combined", return_value=fake_result):
        result = rescore.handler({}, None)

    assert result["updated"] == 1
    assert result["api_failed_skipped"] == 0
    assert result["semantic_calls"] == 1

    row = db.get_job("test:happy")
    assert row["score"] == 88
    assert row["algo_score"] == 70
    assert row["semantic_score"] == 100
    assert row["semantic_rationale"] == "Strong VP target."
    assert row["work_mode"] == "remote"


# ---------------------------------------------------------------------------
# min_age_hours skip path — row already-fresh, score_combined never called.
# ---------------------------------------------------------------------------

def test_min_age_hours_skips_fresh_rows(aws):
    """A row whose semantic_scored_at is newer than the cutoff is skipped
    entirely — no algo recompute, no semantic call. This is the resume-
    from-partial-rescore pattern documented in the rescore.py docstring."""
    from datetime import datetime, timezone

    fresh_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.put_job(_make_job("test:fresh", semantic_scored_at=fresh_ts))

    with patch("lambdas.rescore.score_combined") as m:
        result = rescore.handler({"min_age_hours": 1.0}, None)

    m.assert_not_called
    assert result["total"] == 1
    assert result["skipped"] == 1
    assert result["updated"] == 0
    assert result["api_failed_skipped"] == 0
