"""qol — deterministic quality-of-life score, 0-100.

Phase R1 of the redesign.  Powers the "Quality of Life" sort option in
the new UI and gives the original author an at-a-glance signal of how comfortable a
role is, independent of fit/comp.

Configurable entirely via `taxonomy.yaml::qol`:
    qol:
      weights:
        work_mode_remote:     25
        work_mode_hybrid:     15
        salary_listed:        20
        salary_above_floor:   15
        posted_recent:        10
        equity_keywords:      10
        benefits_keywords:    10
        flexibility_keywords: 10
      salary_floor: 175000
      posted_recent_days: 14
      keywords:
        equity:      [...]
        benefits:    [...]
        flexibility: [...]

Public API:
    score_qol(job: dict) -> dict
        Returns {"score": int 0-100, "breakdown": dict[str, int]}
        Breakdown shows each weight that fired so the UI can hover-explain
        why a job got a 75 vs a 40.

Why deterministic (no LLM call)?
    * Free + instant — runs in the same Lambda pass as the algo score.
    * the original author-tunable: edit weights in the YAML, redeploy taxonomy.yaml,
      next rescore picks them up.  No prompt iteration loop.
    * Stable: no semantic drift between Haiku versions changes the
      meaning of "good QoL".

Edge cases handled:
    * Missing / null fields silently contribute zero (never raises).
    * Decimal salaries (DynamoDB rehydration) are coerced to int.
    * Date parsing falls back gracefully if posted_at isn't ISO.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional

from .taxonomy import TAXONOMY


# ---------------------------------------------------------------------
# Pull all qol config out once at module import.  Defaults are sane in
# case taxonomy.yaml is missing the section entirely (test fixtures).
# ---------------------------------------------------------------------
_QOL = TAXONOMY.get("qol", {}) or {}
_W = _QOL.get("weights", {}) or {}

W_REMOTE       = int(_W.get("work_mode_remote",     25))
W_HYBRID       = int(_W.get("work_mode_hybrid",     15))
W_SAL_LISTED   = int(_W.get("salary_listed",        20))
W_SAL_FLOOR    = int(_W.get("salary_above_floor",   15))
W_POSTED       = int(_W.get("posted_recent",        10))
W_EQUITY       = int(_W.get("equity_keywords",      10))
W_BENEFITS     = int(_W.get("benefits_keywords",    10))
W_FLEXIBILITY  = int(_W.get("flexibility_keywords", 10))

SALARY_FLOOR        = int(_QOL.get("salary_floor", 175_000))
POSTED_RECENT_DAYS  = int(_QOL.get("posted_recent_days", 14))

_KW = (_QOL.get("keywords") or {})
KW_EQUITY      = [k.lower for k in _KW.get("equity", )]
KW_BENEFITS    = [k.lower for k in _KW.get("benefits", )]
KW_FLEXIBILITY = [k.lower for k in _KW.get("flexibility", )]


# ---------------------------------------------------------------------
# Tiny helpers — all guard against None / missing fields silently.
# ---------------------------------------------------------------------

def _to_int(val) -> Optional[int]:
    """Coerce DynamoDB Decimal / str / int → int, or None on failure."""
    if val is None:
        return None
    if isinstance(val, (int, float, Decimal)):
        try:
            return int(val)
        except (ValueError, TypeError):
            return None
    if isinstance(val, str) and val.strip:
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return None
    return None


def _any_kw(text: str, kws: list) -> bool:
    return any(kw in text for kw in kws)


def _is_recent(posted_at: Optional[str], days: int) -> bool:
    """True if posted_at is within `days` of now.  ISO-Z format expected."""
    if not posted_at:
        return False
    try:
        # Strip any sub-second precision; we only care about day granularity.
        dt = datetime.strptime(posted_at[:19], "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return False
    return datetime.now(timezone.utc) - dt < timedelta(days=days)


# ---------------------------------------------------------------------
# Public scorer.
# ---------------------------------------------------------------------

def score_qol(job: dict) -> dict:
    """Return {'score': int, 'breakdown': dict}.

    Breakdown maps each fired-weight name → its contribution.  The UI
    surfaces this in a tooltip on the QoL chip so the original author can see at a
    glance why a job rated where it did.
    """
    breakdown: dict = {}

    # ----- Work mode ----------------------------------------------------
    # Mutually exclusive: remote OR hybrid OR neither.  Onsite/unclear
    # contribute zero on purpose — they're neutral, not negative.
    work_mode = (job.get("work_mode") or "").lower.strip
    if work_mode == "remote":
        breakdown["work_mode_remote"] = W_REMOTE
    elif work_mode == "hybrid":
        breakdown["work_mode_hybrid"] = W_HYBRID

    # ----- Salary transparency + floor ---------------------------------
    sal_min = _to_int(job.get("salary_min"))
    sal_max = _to_int(job.get("salary_max"))
    if (sal_min and sal_min > 0) or (sal_max and sal_max > 0):
        breakdown["salary_listed"] = W_SAL_LISTED
        # Floor uses min when present, else max as the optimistic proxy.
        # (A role with only a max published is rare — usually it's min.)
        floor_proxy = sal_min if sal_min and sal_min > 0 else sal_max
        if floor_proxy and floor_proxy >= SALARY_FLOOR:
            breakdown["salary_above_floor"] = W_SAL_FLOOR

    # ----- Posted recency ----------------------------------------------
    if _is_recent(job.get("posted_at"), POSTED_RECENT_DAYS):
        breakdown["posted_recent"] = W_POSTED

    # ----- Description keyword scans -----------------------------------
    desc = (job.get("description") or "").lower
    if KW_EQUITY and _any_kw(desc, KW_EQUITY):
        breakdown["equity_keywords"] = W_EQUITY
    if KW_BENEFITS and _any_kw(desc, KW_BENEFITS):
        breakdown["benefits_keywords"] = W_BENEFITS
    if KW_FLEXIBILITY and _any_kw(desc, KW_FLEXIBILITY):
        breakdown["flexibility_keywords"] = W_FLEXIBILITY

    total = min(100, max(0, sum(breakdown.values)))
    return {"score": total, "breakdown": breakdown}
