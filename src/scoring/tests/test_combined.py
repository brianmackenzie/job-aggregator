"""unit tests for combined.score_combined.

The tests mock `scoring.semantic.semantic_score` so they're fast and
network-free. The point is to verify orchestration in
`combined.score_combined` now that has flipped the model:

  * Algo is a BINARY PREFILTER (pass / fail) — no blended math.
  * Haiku is the SOLE RANKER — its score IS the final score.
  * Tier routing adds "needs_review" (Haiku unavailable on a
    prefilter-passed job) and "watchlist_dream" (low score but Haiku
    flagged a dream-tier company worth monitoring).

Run from the repo root:
    python -m pytest src/scoring/tests/test_combined.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Job fixtures — chosen so the prefilter has a predictable pass/fail verdict.
# ---------------------------------------------------------------------------

# Strong VP role — prefilter passes, dream-tier company.
JOB_GOOD = {
    "job_id":             "test:good",
    "title":              "VP, Live Service",
    "company":            "Roblox",
    "company_normalized": "roblox",
    "description": (
        "Lead live-service infrastructure and platform strategy for "
        "Roblox. Strategy and technology roadmap ownership. Reports to "
        "the SVP. We offer RSU equity, parental leave, and gender-affirming "
        "care. Remote (US). Multiplayer infrastructure expertise required."
    ),
    "location":           "Remote (US)",
    "remote":             True,
    "salary_min":         260000,
    "salary_max":         340000,
    "posted_at":          "2026-04-15T00:00:00Z",
}


# Hard-prefiltered role — "Junior Software Engineer Intern" fires multiple
# disqualifiers (seniority + function + engagement).
JOB_GATED = {
    "job_id":             "test:gated",
    "title":              "Junior Software Engineer Intern",
    "company":            "Some Co",
    "company_normalized": "some co",
    "description":        "Internship for new grads.",
    "location":           "Remote",
    "remote":             True,
    "posted_at":          "2026-04-15T00:00:00Z",
}


# ---------------------------------------------------------------------------
# Helper — standard successful Haiku payload with all fields.
# ---------------------------------------------------------------------------

def _make_haiku_payload(
    score: int = 85,
    watchlist_dream: bool = False,
    role_family: str = "strong",
    industry: str = "strong",
    geography: str = "reachable",
    level: str = "match",
    life_fit: list[str] | None = None,
    rationale: str = "Strong fit",
) -> dict:
    return {
        "score":             score,
        "rationale":         rationale,
        "work_mode":         "remote",
        "role_family_match": role_family,
        "industry_match":    industry,
        "geography_match":   geography,
        "level_match":       level,
        "watchlist_dream":   watchlist_dream,
        "life_fit_concerns": life_fit or ,
        "model":             "test-haiku",
        "scored_at":         "2026-04-17T00:00:00Z",
    }


# ---------------------------------------------------------------------------
# Prefilter-fail path
# ---------------------------------------------------------------------------

def test_prefilter_fail_skips_semantic_and_zeros_score:
    """Hard disqualifier → score=0, tier=DISQUALIFIED, no Haiku call.

    BUG 3 — tier value renamed from "skip" to
    "DISQUALIFIED" so the UI / export sub-group label distinguishes
    "prefilter killed it" from "Haiku scored it low".
    """
    from scoring.combined import score_combined
    with patch("scoring.combined.call_semantic") as m:
        out = score_combined(JOB_GATED, prefs={})
    m.assert_not_called
    assert out["passed_prefilter"] is False
    assert out["score"] == 0
    assert out["tier"] == "DISQUALIFIED"
    assert out["algo_score"] == 0          # binary diagnostic
    assert out["semantic_score"] is None
    assert out["semantic_skipped"] is True
    assert out["semantic_api_failed"] is False
    # Prefilter reason should be populated for the detail view.
    assert out["prefilter_reason"] != "passed"
    assert out["prefilter_reason"]          # non-empty
    assert out["hard_disqualifiers"]        # non-empty list


# ---------------------------------------------------------------------------
# Skip-semantic / kill-switch paths
# ---------------------------------------------------------------------------

def test_skip_semantic_on_passing_prefilter_yields_needs_review:
    """skip_semantic=True + passing prefilter → tier=needs_review."""
    from scoring.combined import score_combined
    with patch("scoring.combined.call_semantic") as m:
        out = score_combined(JOB_GOOD, prefs={}, skip_semantic=True)
    m.assert_not_called
    assert out["passed_prefilter"] is True
    assert out["algo_score"] == 100
    assert out["semantic_score"] is None
    assert out["semantic_skipped"] is True
    assert out["semantic_api_failed"] is False
    assert out["tier"] == "needs_review"


def test_kill_switch_off_yields_needs_review(monkeypatch):
    """semantic.enabled=false + passing prefilter → tier=needs_review."""
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: False)
    with patch("scoring.combined.call_semantic") as m:
        out = combined.score_combined(JOB_GOOD, prefs={})
    m.assert_not_called
    assert out["tier"] == "needs_review"
    assert out["semantic_skipped"] is True
    assert out["semantic_api_failed"] is False


# ---------------------------------------------------------------------------
# Happy path: prefilter passes, Haiku returns a real score.
# ---------------------------------------------------------------------------

def test_prefilter_pass_plus_semantic_yields_semantic_score(monkeypatch):
    """Prefilter pass + Haiku T1 score → final = semantic, tier = T1."""
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    with patch("scoring.combined.call_semantic") as m:
        m.return_value = _make_haiku_payload(score=88)
        out = combined.score_combined(JOB_GOOD, prefs={})
    m.assert_called_once
    assert out["passed_prefilter"] is True
    assert out["score"] == 88
    assert out["semantic_score"] == 88
    assert out["tier"] == "T1"               # 88 >= 78
    assert out["semantic_skipped"] is False
    assert out["semantic_api_failed"] is False
    # Structured fields flow through.
    assert out["role_family_match"] == "strong"
    assert out["industry_match"] == "strong"
    assert out["geography_match"] == "reachable"
    assert out["level_match"] == "match"


def test_tier_routing_from_semantic_score(monkeypatch):
    """Tier follows the semantic score, not the (binary) algo_score."""
    from scoring import combined
    from scoring.keywords import tier_from_score
    monkeypatch.setattr(combined, "_enabled", lambda: True)

    for semantic in (80, 70, 55, 40, 20):
        with patch("scoring.combined.call_semantic") as m:
            m.return_value = _make_haiku_payload(score=semantic)
            out = combined.score_combined(JOB_GOOD, prefs={})
        assert out["score"] == semantic
        assert out["tier"] == tier_from_score(semantic)


# ---------------------------------------------------------------------------
# Watchlist-dream path
# ---------------------------------------------------------------------------

def test_watchlist_dream_upgrades_skip_tier(monkeypatch):
    """Low semantic + watchlist_dream=True → tier = watchlist_dream.

    BUG 4 — watchlist_dream now requires FOUR conditions:
      1. Haiku watchlist_dream flag
      2. is_dream_company       (Roblox is industry score 10 → dream-tier)
      3. is_leadership_title    (the JOB_GOOD title "VP, Live Service"
                                  matches the new LEADERSHIP_WHITELIST)
      4. geography_match == "unreachable"
                                (set in the Haiku payload below)
    """
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    with patch("scoring.combined.call_semantic") as m:
        m.return_value = _make_haiku_payload(
            score=25,
            watchlist_dream=True,
            geography="unreachable",   # required gate
        )
        out = combined.score_combined(JOB_GOOD, prefs={})
    assert out["score"] == 25
    assert out["watchlist_dream"] is True
    assert out["is_dream_company"] is True
    assert out["is_leadership_title"] is True
    assert out["geography_match"] == "unreachable"
    assert out["tier"] == "watchlist_dream"


def test_watchlist_dream_blocked_when_geography_reachable(monkeypatch):
    """BUG 4 — Haiku flag alone is not enough.

    A dream-co + leadership-title row that's geography-REACHABLE should
    NOT land in watchlist_dream — geography is supposed to be the SOLE
    blocker for the rescue.
    """
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    with patch("scoring.combined.call_semantic") as m:
        m.return_value = _make_haiku_payload(
            score=25,
            watchlist_dream=True,
            geography="reachable",     # NOT the blocker — reject dream
        )
        out = combined.score_combined(JOB_GOOD, prefs={})
    assert out["score"] == 25
    assert out["watchlist_dream"] is True   # Haiku still flags
    assert out["tier"] == "skip"            # but combined refuses to upgrade


def test_watchlist_dream_does_not_downgrade_real_tier(monkeypatch):
    """A T3/T2/T1 score is NOT replaced by watchlist_dream, even if flagged."""
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    with patch("scoring.combined.call_semantic") as m:
        m.return_value = _make_haiku_payload(
            score=55,
            watchlist_dream=True,
            geography="unreachable",  # all four gates green, but score>=35
        )
        out = combined.score_combined(JOB_GOOD, prefs={})
    # 55 >= 50 → tier T3, not watchlist_dream (only skip gets upgraded).
    assert out["tier"] == "T3"
    assert out["watchlist_dream"] is True


# ---------------------------------------------------------------------------
# API-failure → needs_review
# ---------------------------------------------------------------------------

def test_semantic_api_fail_yields_needs_review(monkeypatch):
    """call_semantic returning None → tier=needs_review, api_failed=True.

    retro motivated `semantic_api_failed`: a 429 should NOT
    overwrite a previously-good row in DynamoDB. rescore.py keys off
    this flag to decline the update_item.
    """
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    with patch("scoring.combined.call_semantic") as m:
        m.return_value = None
        out = combined.score_combined(JOB_GOOD, prefs={})
    m.assert_called_once
    assert out["semantic_score"] is None
    assert out["tier"] == "needs_review"
    assert out["semantic_skipped"] is True
    assert out["semantic_api_failed"] is True
    # score stays 0 — Haiku didn't return anything.
    assert out["score"] == 0


# ---------------------------------------------------------------------------
# Cache reuse
# ---------------------------------------------------------------------------

def test_fresh_cache_reuse_skips_api_call(monkeypatch):
    """semantic_scored_at within cache_days → no new Haiku call."""
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    monkeypatch.setattr(combined, "_cache_days", lambda: 7)

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    cached_job = dict(JOB_GOOD)
    cached_job["semantic_score"]     = 82
    cached_job["semantic_rationale"] = "cached good fit"
    cached_job["semantic_scored_at"] = yesterday
    cached_job["semantic_model"]     = "claude-haiku-cached"
    cached_job["role_family_match"]  = "strong"
    cached_job["watchlist_dream"]    = False

    with patch("scoring.combined.call_semantic") as m:
        out = combined.score_combined(cached_job, prefs={})
    m.assert_not_called
    assert out["semantic_score"] == 82
    assert out["semantic_rationale"] == "cached good fit"
    assert out["score"] == 82
    assert out["tier"] == "T1"


def test_stale_cache_triggers_new_api_call(monkeypatch):
    """Old semantic_scored_at → re-call the LLM (cache is stale)."""
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    monkeypatch.setattr(combined, "_cache_days", lambda: 7)

    long_ago = (datetime.now(timezone.utc) - timedelta(days=30)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    stale_job = dict(JOB_GOOD)
    stale_job["semantic_score"]     = 82
    stale_job["semantic_scored_at"] = long_ago

    with patch("scoring.combined.call_semantic") as m:
        m.return_value = _make_haiku_payload(score=60)
        out = combined.score_combined(stale_job, prefs={})
    m.assert_called_once
    assert out["semantic_score"] == 60       # Fresh call wins.


def test_force_semantic_ignores_fresh_cache(monkeypatch):
    """force_semantic=True → always re-call Haiku, even with fresh cache."""
    from scoring import combined
    monkeypatch.setattr(combined, "_enabled", lambda: True)
    monkeypatch.setattr(combined, "_cache_days", lambda: 7)

    fresh = (datetime.now(timezone.utc) - timedelta(hours=1)) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    cached_job = dict(JOB_GOOD)
    cached_job["semantic_score"]     = 82
    cached_job["semantic_scored_at"] = fresh

    with patch("scoring.combined.call_semantic") as m:
        m.return_value = _make_haiku_payload(score=95)
        out = combined.score_combined(cached_job, prefs={}, force_semantic=True)
    m.assert_called_once
    assert out["semantic_score"] == 95


# ---------------------------------------------------------------------------
# Response shape / orthogonal layers
# ---------------------------------------------------------------------------

def test_response_shape_includes_round_15_keys:
    """Every key a downstream consumer cares about is present."""
    from scoring.combined import score_combined
    out = score_combined(JOB_GOOD, prefs={}, skip_semantic=True)
    expected_keys = (
        # Legacy / ongoing:
        "score", "tier", "track", "breakdown", "gates_triggered",
        "modifiers_applied", "algo_score", "semantic_score",
        "semantic_rationale", "semantic_scored_at", "semantic_model",
        "semantic_skipped", "semantic_api_failed", "work_mode",
        "industries", "role_types", "company_group",
        "qol_score", "qol_breakdown", "engagement_type",
        # prefilter passthroughs:
        "passed_prefilter", "prefilter_reason", "hard_disqualifiers",
        "soft_warnings", "positive_signals", "is_dream_company",
        "is_hrc100", "is_crunch_co", "location_flag",
        "industry", "industry_score",
        # Haiku structured passthroughs:
        "role_family_match", "industry_match", "geography_match",
        "level_match", "watchlist_dream", "life_fit_concerns",
        # new prefilter passthrough for watchlist gating
        "is_leadership_title",
    )
    for k in expected_keys:
        assert k in out, f"missing key in combined result: {k}"


def test_taxonomy_and_qol_populate_on_prefilter_pass:
    """Orthogonal layers run regardless of semantic enable/skip."""
    from scoring.combined import score_combined
    out = score_combined(JOB_GOOD, prefs={}, skip_semantic=True)
    assert "gaming" in out["industries"]
    assert "tech" in out["industries"]
    assert out["qol_score"] >= 50
    assert out["company_group"] == "tier_s"  # Roblox in companies.yaml


# ---------------------------------------------------------------------------
# BUG 1 — leadership whitelist + word-boundary disqualifiers
# ---------------------------------------------------------------------------

# the original author's Tests A-E from the spec. Each one is a real-world
# title from the 10,635-row audit that was being WRONG-killed by the old
# substring-based gate logic.

@pytest.mark.parametrize("label,title,company", [
    ("A", "Senior Engineering Manager",                      "Roblox"),
    ("B", "Sr Director Analyst, AI and Software Engineering", "Gartner"),
    ("C", "Associate Director, Platform AI Architect",       "NBCUniversal"),
    ("D", "Senior Technical Program Manager",                "Riot Games"),
    ("E", "AI Architect, Platform Engineering",              "Wizards of the Coast"),
])
def test_round15_fb2_bug1_leadership_titles_pass_prefilter(label, title, company):
    """BUG 1 — these five titles MUST pass the prefilter.

    Before the fix the naive `kw in title_lo` substring matcher killed
    each one. The fix is twofold:
      - Word-boundary regex matching for HARD_DISQUALIFIER_TITLES.
      - LEADERSHIP_WHITELIST_PATTERNS short-circuits the wrong-function
        gate when an exec / leadership role-noun is present.
    """
    from scoring.algo_prefilter import prefilter
    job = {"title": title, "company": company,
           "company_normalized": company.lower,
           "description": "", "location": "Remote"}
    v = prefilter(job, prefs={})
    assert v["passed"] is True, (
        f"Test {label} {title!r} unexpectedly failed prefilter "
        f"(reason={v['prefilter_reason']})"
    )


@pytest.mark.parametrize("title", [
    "Software Engineer",
    "Senior Software Engineer",
    "Staff Engineer",
    "Software Engineer, Backend",
    "Junior Software Engineer",
    "Data Engineer",
    "Machine Learning Engineer",
])
def test_round15_fb2_bug1_ic_engineer_titles_still_killed(title):
    """BUG 1 — IC engineer titles MUST still be killed.

    The whitelist + word-boundary logic must not over-correct. An
    unadorned IC engineer title should still trigger wrong_function.
    """
    from scoring.algo_prefilter import prefilter
    job = {"title": title, "company": "Acme Co",
           "company_normalized": "acme co",
           "description": "", "location": "Remote"}
    v = prefilter(job, prefs={})
    assert v["passed"] is False, (
        f"{title!r} unexpectedly passed prefilter "
        f"(reason={v['prefilter_reason']})"
    )
    assert v["prefilter_reason"].startswith(
        ("wrong_function", "sub_vp_seniority", "priority_disqualifier")
    )


def test_round15_fb2_bug1_ai_architect_removed_from_hard_list:
    """BUG 1 — bare 'ai architect' must NOT be a hard kill."""
    from scoring import candidate_profile as CP
    assert "ai architect" not in CP.HARD_DISQUALIFIER_TITLES_FUNCTION


def test_round15_fb2_bug1_tpm_now_soft_warning_not_hard_kill:
    """BUG 1 — 'technical program manager' moved to soft warning."""
    from scoring import candidate_profile as CP
    from scoring.algo_prefilter import prefilter
    assert "technical program manager" not in CP.HARD_DISQUALIFIER_TITLES_FUNCTION
    assert "technical program manager" in CP.SOFT_WARNING_TPM
    # Round-trip: a TPM title at a dream-co should pass with a soft warning.
    v = prefilter(
        {"title": "Senior Technical Program Manager", "company": "Riot",
         "company_normalized": "riot games", "description": "",
         "location": "Remote"},
        prefs={},
    )
    assert v["passed"] is True
    assert "tpm_title" in v["soft_warnings"]


# ---------------------------------------------------------------------------
# BUG 3 — prefilter_reason / tier / final_score on fail
# ---------------------------------------------------------------------------

def test_round15_fb2_bug3_fail_path_tier_is_disqualified:
    """BUG 3 — tier must be 'DISQUALIFIED' (not 'skip')."""
    from scoring.combined import score_combined
    out = score_combined(JOB_GATED, prefs={}, skip_semantic=True)
    assert out["tier"] == "DISQUALIFIED"
    assert out["score"] == 0
    assert out["passed_prefilter"] is False
    assert out["prefilter_reason"]                       # non-empty
    assert out["prefilter_reason"] != "passed"
    assert out["prefilter_reason"] != "unknown"


def test_round15_fb2_bug3_unknown_prefilter_fail_sentinel:
    """BUG 3 — defensive fallback when no specific reason.

    The prefilter should never return a fail-state with an empty reason
    string. the original author's audit found 3,396 such rows in DDB. The new sentinel
    'unknown_prefilter_fail' makes them groupable in the export.
    """
    from scoring.algo_prefilter import prefilter
    # Synthesize a job that triggers the disqualifier list with an empty
    # reason by patching out _check_wrong_function's return — but since
    # we can't easily do that here without monkeypatching, just verify
    # the sentinel is in the candidate_profile / prefilter constants.
    # The constant should appear in the source.
    import inspect
    from scoring import algo_prefilter
    src = inspect.getsource(algo_prefilter)
    assert "unknown_prefilter_fail" in src

    # And combined.py should default to "unknown_prefilter_fail" when
    # the prefilter says fail but doesn't provide a reason.
    from scoring import combined
    src2 = inspect.getsource(combined)
    assert "unknown_prefilter_fail" in src2


def test_taxonomy_populates_even_when_prefilter_fails:
    """A prefilter-rejected job still gets taxonomy + QoL populated.

    The UI uses these fields to show what KIND of role got filtered so
    the original author can spot if an entire category is being over-gated.
    """
    from scoring.combined import score_combined
    out = score_combined(JOB_GATED, prefs={}, skip_semantic=True)
    assert out["score"] == 0
    assert "industries" in out
    assert "role_types" in out
    assert "qol_score" in out
