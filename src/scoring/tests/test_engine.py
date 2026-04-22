"""Golden fixture tests for the scoring engine.

Three fixtures whose expected scores are LOCKED IN. A failing test here
means a weight, keyword, or gate change altered behaviour that was previously
accepted as correct. Investigate before updating the expected values.

Run locally (from repo root):
    python -m pytest src/scoring/tests/ -v

These tests import the engine directly — no AWS, no DynamoDB, no network.
"""
import pytest

# Import via package path. Works because conftest.py in src/ adds src/ to sys.path.
from scoring.engine import score


# ---------------------------------------------------------------------------
# Fixture 1 — Tier 1 gaming VP (must score very high, no gates)
#
# VP of Product at Riot Games: strong gaming industry (score=10), fully
# remote US (score=10), six-figure salary with RSUs, M&A integration content,
# gaming culture signals, company_tier="1". Riot is on the HRC 100.
#
# This fixture tests that a near-perfect gaming exec role surfaces at the
# top of the feed. A regression that drops it below T1 (score < 78) is a bug.
#
# GOLDEN_SCORE math (under the default lifestyle weights and the
# default comp_thresholds in scoring.yaml):
#   role_fit (6.83×.20) + industry (10×.18) + geo (10×.16) + comp (7.0×.15)
#   + wlq (6×.12) + passion (6×.06) + cultural (10×.06) + health (7×.04)
#   + career (7.25×.03) + engagement (9×.00) ≈ 7.995
#   × 10 ≈ 80.0
#   modifiers: +6 (tier 1) +7 (M&A × gaming) +5 (remote VP gaming) = +18
#   final = clamp(80.0 + 18) = 98
#
# The compensation 7.0 (vs. a hypothetical 7.5) reflects the fixture's
# $220-260K landing in the "overpay" band of the default thresholds.
# If you re-tune `static_lists.comp_thresholds` in scoring.yaml so that
# $220-260K sits in the "high" band instead, the comp category will
# climb and GOLDEN_SCORE may need to bump up by 1.
# ---------------------------------------------------------------------------
JOB_VP_GAMING = {
    "title": "VP of Product",
    "company": "Riot Games",
    "company_normalized": "riot games",
    "description": (
        "Lead product strategy for the gaming platform. "
        "Technology roadmap definition and governance. "
        "Sponsor architecture review board for live service titles. "
        "Post-acquisition integration of studio technologies into our platform. "
        "Drive esports and free-to-play experiences. "
        "For the players. "
        "Based remote, anywhere in the United States. "
        "Salary $220,000 - $260,000 base plus annual bonus and RSUs."
    ),
    "location": "Remote, United States",
    "remote": True,
    "salary_min": 220_000,
    "salary_max": 260_000,
    "company_tier": "1",   # Pre-fetched from Companies table by scrape worker
    "status": "active",
    "posted_at": "2026-04-15T10:00:00Z",
}

# ---------------------------------------------------------------------------
# Fixture 2 — IC analyst role hits the function gate
#
# Senior Data Analyst at ESPN (sports analytics, NYC hybrid, no salary).
# "Data Analyst" is an IC analytics function — categorically wrong-fit for
# the example VP-technology profile encoded in the default scoring config.
# "data analyst" is a function-gate keyword, so this fixture is expected
# to produce score = 0 with gates_triggered = ["function"].
#
# GOLDEN_SCORE:
#   function gate fires → final = 0 regardless of category signals.
# ---------------------------------------------------------------------------
JOB_ANALYST_ESPN = {
    "title": "Senior Data Analyst",
    "company": "ESPN",
    "company_normalized": "espn",
    "description": (
        "Senior data analyst position in sports analytics. "
        "Business intelligence and competitive intelligence reporting. "
        "Strategy and market analysis for linear and streaming products. "
        "Based in New York, NY. Hybrid, 3 days in office."
    ),
    "location": "New York, NY",
    "remote": False,
    "salary_min": None,
    "salary_max": None,
    "company_tier": None,
    "status": "active",
    "posted_at": "2026-04-14T09:00:00Z",
}

# ---------------------------------------------------------------------------
# Fixture 3 — Hard-gated intern role (must score exactly 0)
#
# "Marketing Intern" — the seniority gate fires on the title keyword "intern"
# and forces score=0 regardless of all other signals. The feed must never
# show intern listings under the default seniority floor.
# ---------------------------------------------------------------------------
JOB_INTERN_GATED = {
    "title": "Marketing Intern",
    "company": "Random Corp",
    "company_normalized": "random corp",
    "description": (
        "Exciting summer internship opportunity. "
        "Work closely with our marketing team on brand strategy. "
        "New York, NY."
    ),
    "location": "New York, NY",
    "remote": False,
    "salary_min": None,
    "salary_max": None,
    "company_tier": None,
    "status": "active",
    "posted_at": "2026-04-13T08:00:00Z",
}

# Shared prefs — empty by default; per-user personalisation hooks live
# under `/api/prefs` and are not exercised by these golden fixtures.
PREFS: dict = {}


# ---------------------------------------------------------------------------
# Test 1: VP gaming role — Tier 1 / Apply Immediately
# ---------------------------------------------------------------------------

def test_vp_gaming_scores_tier1:
    """VP of Product at Riot Games (remote, $260k, gaming) must score Tier 1."""
    result = score(JOB_VP_GAMING, PREFS)

    # GOLDEN SCORE — see the fixture docstring for the per-category math.
    GOLDEN_SCORE = 98

    assert result["score"] == GOLDEN_SCORE, (
        f"GOLDEN SCORE CHANGED: expected {GOLDEN_SCORE}, got {result['score']}.\n"
        f"  Breakdown:  {result['breakdown']}\n"
        f"  Modifiers:  {result['modifiers_applied']}\n"
        f"  Gates:      {result['gates_triggered']}"
    )
    assert result["tier"] == "T1", \
        f"Expected Tier 1 (score >= 78), got tier={result['tier']}"
    assert result["track"] == "TRACK_1_FULLTIME", \
        f"VP full-time role should be TRACK_1_FULLTIME, got {result['track']}"
    assert result["gates_triggered"] == , \
        f"No gates should fire for this role. Got: {result['gates_triggered']}"

    # --- Category score sanity checks ---
    breakdown = result["breakdown"]

    # Riot Games must map to gaming_publisher_platform → score 10.
    assert breakdown["industry_alignment"] == 10.0, (
        "Riot Games should map to gaming_publisher_platform (score=10). "
        f"Got {breakdown['industry_alignment']}"
    )
    # Fully remote US must produce geographic score 10.
    assert breakdown["geographic"] == 10.0, (
        "Fully remote US role should produce geographic score 10. "
        f"Got {breakdown['geographic']}"
    )

    # --- Modifier sanity checks ---
    mods = result["modifiers_applied"]
    assert "remote_vp_gaming" in mods, \
        f"remote_vp_gaming modifier must fire (remote VP at gaming co). Got: {mods}"
    assert "ma_gaming_media" in mods, \
        f"ma_gaming_media modifier must fire (M&A integration at gaming co). Got: {mods}"
    assert "company_tier_1" in mods, \
        f"company_tier_1 modifier must fire (company_tier='1'). Got: {mods}"


# ---------------------------------------------------------------------------
# Test 2: Mid-tier analyst role — Tier 3 / Monitor
# ---------------------------------------------------------------------------

def test_analyst_espn_scores_midtier:
    """Senior Data Analyst at ESPN must be FUNCTION-GATED to 0.

    "data analyst" is on the default function-gate keyword list — IC
    analytics roles are categorically wrong-fit for the example
    VP-technology profile encoded in the default scoring config,
    regardless of company or industry signals. This test guards the
    gate behavior, not the score itself (the score is always 0 when a
    hard gate fires).
    """
    result = score(JOB_ANALYST_ESPN, PREFS)

    # GOLDEN SCORE — function gate fires → 0.
    GOLDEN_SCORE = 0

    assert result["score"] == GOLDEN_SCORE, (
        f"GOLDEN SCORE CHANGED: expected {GOLDEN_SCORE}, got {result['score']}.\n"
        f"  Breakdown:  {result['breakdown']}\n"
        f"  Modifiers:  {result['modifiers_applied']}\n"
        f"  Gates:      {result['gates_triggered']}"
    )
    # 0 falls in the skip bucket.
    assert result["tier"] == "skip", \
        f"Expected 'skip' (score=0), got tier={result['tier']}"
    assert "function" in result["gates_triggered"], (
        "function gate must fire on 'Senior Data Analyst' (IC analytics role). "
        f"Got gates: {result['gates_triggered']}"
    )
    # Engine short-circuits before scoring categories — breakdown is empty.
    assert result["breakdown"] == {}, \
        "breakdown must be empty dict for hard-gated roles"
    assert result["modifiers_applied"] == , \
        "modifiers_applied must be empty for hard-gated roles"


# ---------------------------------------------------------------------------
# Test 3: Hard-gated intern role — score must be exactly 0
# ---------------------------------------------------------------------------

def test_intern_is_hard_gated:
    """Intern role must score exactly 0 due to the seniority hard gate."""
    result = score(JOB_INTERN_GATED, PREFS)

    assert result["score"] == 0, (
        f"Intern role MUST score 0. Got {result['score']}. "
        "The seniority gate may have stopped firing — check gates.py."
    )
    assert "seniority" in result["gates_triggered"], (
        f"'seniority' gate must be in gates_triggered. "
        f"Got: {result['gates_triggered']}"
    )
    assert result["tier"] == "skip", \
        f"Hard-gated roles must be tier 'skip'. Got {result['tier']}"

    # Engine short-circuits before scoring — these must be empty.
    assert result["breakdown"] == {}, \
        "breakdown must be empty dict for hard-gated roles"
    assert result["modifiers_applied"] == , \
        "modifiers_applied must be empty for hard-gated roles"
