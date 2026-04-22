"""Unit tests for src/scoring/qol.py.

The QoL score is fully deterministic, so these tests assert exact
values per fixture.  Each test isolates one or two signal weights
to keep the arithmetic easy to read.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from scoring import qol


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# ---------------------------------------------------------------------
# Boundary cases
# ---------------------------------------------------------------------

def test_empty_job_scores_zero:
    out = qol.score_qol({})
    assert out["score"] == 0
    assert out["breakdown"] == {}


def test_job_with_only_onsite_scores_zero:
    """Onsite/unclear contribute nothing — they're neutral, not penalized."""
    out = qol.score_qol({"work_mode": "onsite"})
    assert out["score"] == 0


# ---------------------------------------------------------------------
# Single-signal isolation
# ---------------------------------------------------------------------

def test_remote_alone_scores_25:
    out = qol.score_qol({"work_mode": "remote"})
    assert out["score"] == qol.W_REMOTE
    assert out["breakdown"] == {"work_mode_remote": qol.W_REMOTE}


def test_hybrid_alone_scores_15:
    out = qol.score_qol({"work_mode": "hybrid"})
    assert out["score"] == qol.W_HYBRID


def test_salary_listed_below_floor_only_gets_listed_credit:
    out = qol.score_qol({"salary_min": 100_000})
    # Listed yes, but below floor → no above_floor credit.
    assert "salary_listed" in out["breakdown"]
    assert "salary_above_floor" not in out["breakdown"]
    assert out["score"] == qol.W_SAL_LISTED


def test_salary_at_or_above_floor_gets_both_credits:
    out = qol.score_qol({"salary_min": qol.SALARY_FLOOR})
    assert out["breakdown"]["salary_listed"] == qol.W_SAL_LISTED
    assert out["breakdown"]["salary_above_floor"] == qol.W_SAL_FLOOR
    assert out["score"] == qol.W_SAL_LISTED + qol.W_SAL_FLOOR


def test_salary_max_only_with_zero_min_uses_max_as_floor_proxy:
    """When only a max is published (rare but real), use it as the
    floor proxy — better-than-nothing optimistic read."""
    out = qol.score_qol({"salary_min": 0, "salary_max": qol.SALARY_FLOOR + 50_000})
    assert out["breakdown"]["salary_listed"] == qol.W_SAL_LISTED
    assert out["breakdown"]["salary_above_floor"] == qol.W_SAL_FLOOR


def test_decimal_salary_field_is_handled:
    """DynamoDB rehydrates numeric attrs as Decimal — must not crash."""
    out = qol.score_qol({"salary_min": Decimal("250000")})
    assert out["breakdown"]["salary_above_floor"] == qol.W_SAL_FLOOR


def test_recently_posted_gets_credit:
    out = qol.score_qol({"posted_at": _iso_days_ago(1)})
    assert out["breakdown"]["posted_recent"] == qol.W_POSTED


def test_old_post_does_not_get_recency_credit:
    out = qol.score_qol({"posted_at": _iso_days_ago(qol.POSTED_RECENT_DAYS + 5)})
    assert "posted_recent" not in out["breakdown"]


def test_malformed_posted_at_is_silently_ignored:
    out = qol.score_qol({"posted_at": "not-a-date"})
    assert "posted_recent" not in out["breakdown"]
    assert out["score"] == 0


def test_equity_keyword_match:
    out = qol.score_qol({"description": "RSU grant on a 4-year vest."})
    assert out["breakdown"]["equity_keywords"] == qol.W_EQUITY


def test_benefits_keyword_match:
    out = qol.score_qol({"description": "16 weeks of paid parental leave."})
    assert out["breakdown"]["benefits_keywords"] == qol.W_BENEFITS


def test_flexibility_keyword_match:
    out = qol.score_qol({"description": "We work async-first across timezones."})
    assert out["breakdown"]["flexibility_keywords"] == qol.W_FLEXIBILITY


# ---------------------------------------------------------------------
# Combined signals
# ---------------------------------------------------------------------

def test_remote_plus_listed_floor_recent_equity_benefits_flex_clamped_to_100:
    """The maximum-quality fixture: every weight fires.  Sum may exceed 100;
    score is clamped."""
    job = {
        "work_mode":   "remote",
        "salary_min":  300_000,
        "posted_at":   _iso_days_ago(2),
        "description": (
            "Generous RSU grant. 16 weeks of paid parental leave. "
            "Async-first culture, no on-call rotations."
        ),
    }
    out = qol.score_qol(job)
    raw = (
        qol.W_REMOTE + qol.W_SAL_LISTED + qol.W_SAL_FLOOR + qol.W_POSTED
        + qol.W_EQUITY + qol.W_BENEFITS + qol.W_FLEXIBILITY
    )
    assert out["score"] == min(100, raw)


def test_remote_plus_rsu_only:
    """Common case for early-stage SaaS: remote + equity, no comp."""
    out = qol.score_qol({
        "work_mode":   "remote",
        "description": "Significant equity grant.",
    })
    assert out["score"] == qol.W_REMOTE + qol.W_EQUITY


def test_hybrid_overrides_remote_when_set:
    """Mutually exclusive: only one of remote/hybrid/onsite scores."""
    out_r = qol.score_qol({"work_mode": "remote"})
    out_h = qol.score_qol({"work_mode": "hybrid"})
    assert out_r["breakdown"] != out_h["breakdown"]
    assert "work_mode_hybrid" not in out_r["breakdown"]
    assert "work_mode_remote" not in out_h["breakdown"]


# ---------------------------------------------------------------------
# Defensive / error handling
# ---------------------------------------------------------------------

def test_negative_salary_is_treated_as_unlisted:
    """Garbage data shouldn't get credit."""
    out = qol.score_qol({"salary_min": -1, "salary_max": 0})
    assert "salary_listed" not in out["breakdown"]


def test_score_never_exceeds_100:
    """Even if config weights sum >100, output is clamped."""
    job = {
        "work_mode":   "remote",
        "salary_min":  500_000,
        "posted_at":   _iso_days_ago(0),
        "description": (
            "RSU. Parental leave. Async-first. Comprehensive medical. "
            "Flexible hours. No on-call."
        ),
    }
    assert qol.score_qol(job)["score"] <= 100


def test_score_never_negative:
    """Floor at 0 even on adversarial input."""
    out = qol.score_qol({"work_mode": None, "salary_min": None})
    assert out["score"] >= 0
