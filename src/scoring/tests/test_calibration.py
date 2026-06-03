"""calibration tests.

Two modes:
  1. test_calibration_mocked — uses a hand-set semantic score (representative
     of what Haiku would return) and verifies the final score lands in
     the spec's expected range. Runs in CI, no API key needed.

  2. test_calibration_live   — actually calls Haiku. Skipped unless both
     `ANTHROPIC_API_KEY` and `JOBS_RUN_LIVE_SEMANTIC=1` are set. the original author
     runs this locally after editing candidate_profile.yaml to confirm
     the live model produces sane numbers; not part of CI.

Run mocked only:
    python -m pytest src/scoring/tests/test_calibration.py -v
Run mocked + live (requires API key):
    $env:ANTHROPIC_API_KEY = "sk-..."             # PowerShell
    $env:JOBS_RUN_LIVE_SEMANTIC = "1"
    python -m pytest src/scoring/tests/test_calibration.py -v

semantics: the algo layer is a BINARY PREFILTER, not a weighted
scorer. If the prefilter passes, Haiku's score IS the final score — no
blend math, no function-gate rescue. The Roblox VP Platform Engineering
anchor now relies on the candidate_profile.py LEADERSHIP_EXCEPTIONS list
including the "vp platform engineering" phrase (added ) so the
prefilter doesn't mis-gate the title before Haiku ever sees it.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml


# Predicted Haiku scores per anchor — in this IS the final
# score when the prefilter passes, so the anchor window must bracket
# the Haiku value directly (no blend math).
#
# IGT Senior Software Engineer deterministically fails the prefilter
# (IC engineer title + sub-VP seniority), so its semantic is never
# consulted and final=0 regardless of the value here. We still keep a
# plausible Haiku value for the `test_calibration_live` path, where
# it matters if the prefilter is ever bypassed for inspection.
_REPRESENTATIVE_SEMANTIC = {
    "Roblox VP Platform Engineering":      95,  # prefilter pass, sem=final
    "Gartner VP Analyst Gaming Platforms": 82,  # prefilter pass, sem=final
    "Fender D2C Commerce Director":        12,  # prefilter pass, Haiku kills
    "Meow Wolf VP Tech Installations":     88,  # prefilter pass, sem=final
    "IGT Senior Software Engineer":         8,  # prefilter fail → final=0
}


def _load_anchors -> list[dict]:
    """Read the calibration_anchors block from the candidate profile."""
    profile_path = Path(__file__).resolve.parents[3] / "config" / "candidate_profile.yaml"
    with open(profile_path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)["calibration_anchors"]


def _hydrate_job(fixture: dict) -> dict:
    """Add the fields the algo engine expects but the YAML omits."""
    job = dict(fixture)
    job.setdefault("company_normalized", (job.get("company") or "").lower.strip)
    job.setdefault("posted_at", "2026-04-15T00:00:00Z")
    job.setdefault("job_id", f"calibration:{(job.get('company') or 'x').lower}")
    return job


# =====================================================================
# Mocked calibration — runs in CI.
# =====================================================================

@pytest.mark.parametrize("anchor", _load_anchors, ids=lambda a: a["name"])
def test_calibration_mocked(anchor):
    """The prefilter + Haiku pipeline puts each anchor in its expected band.

    no blend math. The prefilter either passes (in which case
    the Haiku score IS the final) or fails (final = 0). This test catches
    regressions in PREFILTER gate logic and PROFILE drift — not in
    Haiku's own behaviour (live test covers that).
    """
    from scoring import combined as _c

    job          = _hydrate_job(anchor["fixture"])
    expected_min = anchor["expected_min"]
    expected_max = anchor["expected_max"]
    sem_score    = _REPRESENTATIVE_SEMANTIC.get(anchor["name"])
    assert sem_score is not None, (
        f"Add an entry for {anchor['name']!r} to _REPRESENTATIVE_SEMANTIC "
        "after editing the candidate profile."
    )

    # Haiku payload — 9 fields required by the new parser.
    fake_payload = {
        "score":             sem_score,
        "rationale":         "mocked",
        "work_mode":         "remote",
        "role_family_match": "strong",
        "industry_match":    "strong",
        "geography_match":   "reachable",
        "level_match":       "match",
        "watchlist_dream":   False,
        "life_fit_concerns": ,
        "model":             "test",
        "scored_at":         "2026-04-17T00:00:00Z",
    }

    # Signature change: call_semantic now takes (job, prefilter_output=None).
    # Lambda accepts a keyword arg + default so mocking stays compatible.
    with patch.object(_c, "_enabled",      lambda: True), \
         patch.object(_c, "call_semantic", lambda j, prefilter_output=None: fake_payload):
        result = _c.score_combined(job, prefs={}, force_semantic=True)

    final = result["score"]
    assert expected_min <= final <= expected_max, (
        f"{anchor['name']}: final={final} (passed_prefilter="
        f"{result['passed_prefilter']}, sem_mocked={sem_score}, "
        f"prefilter_reason={result['prefilter_reason']}), expected "
        f"{expected_min}-{expected_max}. Either the prefilter regressed, "
        f"the profile changed, or the _REPRESENTATIVE_SEMANTIC table "
        f"needs updating."
    )


# =====================================================================
# Live calibration — the original author runs locally with a real API key.
# Requires --runlive AND ANTHROPIC_API_KEY env var. Otherwise skipped.
# =====================================================================

def pytest_addoption(parser):
    """Adds --runlive flag so live tests don't run in CI by default."""
    # This is a no-op when the file is collected as a test module rather
    # than a conftest, but safe to leave for documentation. Real wiring
    # would live in conftest.py if the original author wants to standardise it.
    pass


_RUN_LIVE = os.environ.get("ANTHROPIC_API_KEY") and os.environ.get("JOBS_RUN_LIVE_SEMANTIC")


@pytest.mark.skipif(
    not _RUN_LIVE,
    reason="Set ANTHROPIC_API_KEY and JOBS_RUN_LIVE_SEMANTIC=1 to run live calibration",
)
@pytest.mark.parametrize("anchor", _load_anchors, ids=lambda a: a["name"])
def test_calibration_live(anchor):
    """Actually call Haiku and verify the score lands in the spec window.

    Costs ~$0.0005 per call × 5 anchors = ~$0.0025 per run. Run after
    every candidate_profile.yaml edit.

    subtlety: anchors whose expected band includes 0 (the IGT
    Senior SWE anchor) are designed to FAIL the prefilter, in which case
    Haiku is never called and semantic_score stays None. The only
    behaviour the test can enforce for those is "final score == 0", so
    we skip the sdk-not-None check when the anchor's expected band
    includes 0. For the other anchors, a None semantic_score genuinely
    means the API call failed and the test SHOULD fail with a message
    pointing at API-key / CloudWatch.
    """
    from scoring.combined import score_combined

    job = _hydrate_job(anchor["fixture"])
    result = score_combined(job, prefs={}, force_semantic=True)

    final        = result["score"]
    sem          = result["semantic_score"]
    expected_min = anchor["expected_min"]
    expected_max = anchor["expected_max"]

    # For "designed to be prefilter-killed" anchors (expected_min == 0),
    # a None semantic_score is the correct shape — Haiku was never
    # called. Any other anchor hitting None means the live API call
    # failed, which we want to surface with a clear message before
    # the band check runs.
    if expected_min > 0:
        assert sem is not None, (
            f"{anchor['name']}: live API call returned None "
            "(check ANTHROPIC_API_KEY validity and CloudWatch logs). "
            "This anchor's expected band starts > 0 so a non-None Haiku "
            "score is required."
        )

    assert expected_min <= final <= expected_max, (
        f"{anchor['name']}: live final={final} "
        f"(algo={result['algo_score']}, sem={sem}, "
        f"passed_prefilter={result.get('passed_prefilter')}, "
        f"prefilter_reason={result.get('prefilter_reason')}, "
        f"rationale={result.get('semantic_rationale')!r}), "
        f"expected {expected_min}-{expected_max}."
    )
