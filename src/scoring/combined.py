"""combined (prefilter + Haiku semantic) scorer.

Public entry point:
    score_combined(job: dict, prefs: dict, *, force_semantic=False,
                   skip_semantic=False) -> dict

This is the scorer the rest of the system calls. architecture:

    1. Run the BINARY PREFILTER (algo_prefilter.prefilter) —
       fast, deterministic, free. Sole job: decide if the role is
       worth the cost of a Haiku call.
    2. If the prefilter fails → final score = 0, tier = "DISQUALIFIED"
, skip the LLM entirely. Reason
       surfaced via `prefilter_reason`.
    3. If the prefilter passes → call Haiku (unless kill switch /
       skip_semantic / fresh cache). Haiku is the SOLE RANKER; its
       score IS the final score, no blending.
    4. Tier routing from the final (= semantic) score:
         score >= 78 → "T1"
         score >= 65 → "T2"
         score >= 50 → "T3"
         score >= 35 → "watchlist"
         else          → "skip"
         + if would-be "skip" AND Haiku flagged watchlist_dream=True
           AND is_dream_company AND is_leadership_title AND
           geography_match=="unreachable"  → "watchlist_dream"
           (dream-co tracked for future openings, gated to leadership
            roles where geography is the SOLE blocker — )
         + if Haiku was never callable (API error, kill switch, SDK
           missing) on a prefilter-passed job → "needs_review"

Return shape (keys marked * are new in ):
    {
      "score":              int 0-100      # final = semantic score (0 on fail)
      "tier":               str             # T1|T2|T3|watchlist|skip|
                                            # watchlist_dream|needs_review
      "track":              str             # from prefilter
      "breakdown":          dict            # {"prefilter": <full verdict>}
      "gates_triggered":    list            # prefilter hard_disqualifiers
      "modifiers_applied":  list            # soft_warnings (informational)
      "algo_score":         int|None        # *passed ? 100 : 0  (binary)
      "semantic_score":     int|None        # Haiku score or None
      "semantic_rationale": str|None
      "semantic_scored_at": str|None
      "semantic_model":     str|None
      "semantic_skipped":   bool
      "semantic_api_failed":bool
      "work_mode":          str|None
      "industries":         list            # from taxonomy
      "role_types":         list
      "company_group":      str|None
      "qol_score":          int
      "qol_breakdown":      dict
      "engagement_type":    str
      # Prefilter passthroughs *
      "passed_prefilter":   bool
      "prefilter_reason":   str
      "hard_disqualifiers": list[str]
      "soft_warnings":      list[str]
      "positive_signals":   list[str]
      "is_dream_company":   bool
      "is_hrc100":          bool
      "is_crunch_co":       bool
      "location_flag":      str|None
      "industry":           str|None
      "industry_score":     int|None
      # Haiku structured passthroughs *
      "role_family_match":  str|None
      "industry_match":     str|None
      "geography_match":    str|None
      "level_match":        str|None
      "watchlist_dream":    bool
      "life_fit_concerns":  list[str]
    }

No weighted blending math. No function-gate rescue. The prefilter
decides binary pass/fail; Haiku does all ranking when the prefilter
passes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .algo_prefilter import prefilter as run_prefilter
from .engagement import detect_engagement
from .keywords import CFG, tier_from_score
from .qol import score_qol
from .semantic import semantic_score as call_semantic
from .taxonomy import classify as classify_taxonomy


# ------------------------------------------------------------------
# Config helpers — read from CFG every call so a scoring.yaml edit
# picked up by the rescore Lambda takes effect without redeploy. CFG
# is module-cached at import time but dict lookups are essentially free.
# ------------------------------------------------------------------

def _semantic_cfg -> dict:
    return CFG.get("semantic", {}) or {}


def _enabled -> bool:
    """Kill switch. If False, every job gets tier=needs_review."""
    return bool(_semantic_cfg.get("enabled", False))


def _cache_days -> int:
    """How many days a cached semantic score stays fresh."""
    return int(_semantic_cfg.get("cache_days", 7))


# ------------------------------------------------------------------
# Cache check — has this job been semantic-scored recently?
# Avoids re-burning API credits when a daily scrape re-ingests an
# unchanged posting (most postings linger 2-4 weeks).
# ------------------------------------------------------------------

def _has_fresh_semantic(job: dict) -> bool:
    """True if job already has a semantic_score from within the cache window."""
    semantic_at = job.get("semantic_scored_at")
    if not semantic_at:
        return False
    if "semantic_score" not in job:
        return False
    try:
        # ISO timestamps in this codebase are always "%Y-%m-%dT%H:%M:%SZ".
        scored_at = datetime.strptime(semantic_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except (ValueError, TypeError):
        return False
    age = datetime.now(timezone.utc) - scored_at
    return age < timedelta(days=_cache_days)


def _read_cached_semantic(job: dict) -> Optional[dict]:
    """Pull a previously-stored semantic result back into the response shape.

    the cached row may be from BEFORE the new structured output
    fields existed. Missing fields default to 'unclear' / False /  so
    the caller doesn't have to special-case legacy rows.
    """
    if not _has_fresh_semantic(job):
        return None
    return {
        "score":             int(job.get("semantic_score") or 0),
        "rationale":         job.get("semantic_rationale") or "",
        "model":             job.get("semantic_model") or "",
        "scored_at":         job.get("semantic_scored_at") or "",
        "work_mode":         job.get("work_mode") or "unclear",
        # structured fields (tolerate missing on legacy cache rows).
        "role_family_match": job.get("role_family_match") or "unclear",
        "industry_match":    job.get("industry_match") or "unclear",
        "geography_match":   job.get("geography_match") or "unclear",
        "level_match":       job.get("level_match") or "unclear",
        "watchlist_dream":   bool(job.get("watchlist_dream") or False),
        "life_fit_concerns": list(job.get("life_fit_concerns") or ),
    }


# ------------------------------------------------------------------
# Tier derivation — adds needs_review and watchlist_dream on
# top of the legacy score-based tiers.
# ------------------------------------------------------------------

def _derive_tier(
    *,
    passed_prefilter: bool,
    semantic_available: bool,
    final_score: int,
    watchlist_dream_flag: bool,
    is_dream_company: bool = False,
    is_leadership_title: bool = False,
    geography_match: str = "unclear",
) -> str:
    """Route a job to its tier given prefilter + semantic outcomes.

    The tier names "T1"/"T2"/"T3"/"watchlist" match legacy
    tier_from_score to keep the frontend filter chips stable. 
    states:

    - "DISQUALIFIED"     : prefilter killed the row. renamed
                           from "skip" so the UI sub-group reads as a
                           clear hard-fail bucket distinct from a low
                           Haiku score.
    - "needs_review"     : prefilter passed but Haiku never returned a
                           score (API error, kill switch, SDK missing,
                           parse failure). the original author should hand-rate these
                           rather than have them silently fall into skip.
    - "watchlist_dream"  : gated on three NEW conditions:
                              (1) is_dream_company  (industry score >= 9)
                              (2) is_leadership_title (matches new
                                  LEADERSHIP_WHITELIST_PATTERNS)
                              (3) geography_match == "unreachable"
                                  (i.e. geography is the SOLE blocker)
                           PLUS the original Haiku watchlist_dream flag.
                           This stops the pre-fix over-inclusion of IC
                           roles at dream cos that simply happened to be
                           skip-tier on the Haiku score.
    """
    # BUG 3 — prefilter-fail rows now report tier
    # "DISQUALIFIED" rather than "skip". The frontend / export script
    # treats DISQUALIFIED as a distinct hard-fail bucket separate from a
    # low-Haiku-score skip.
    if not passed_prefilter:
        return "DISQUALIFIED"
    if not semantic_available:
        return "needs_review"
    base = tier_from_score(final_score)
    # BUG 4 — watchlist_dream is now gated on three
    # additional conditions beyond Haiku's flag. The pre-fix logic let
    # any low-Haiku-score row at a dream-co flow into watchlist_dream;
    # the original author's audit found IC engineer roles polluting that bucket. We
    # now require:
    #   - is_dream_company (industry score >= 9 from prefilter)
    #   - is_leadership_title (matches the new LEADERSHIP_WHITELIST)
    #   - geography_match == "unreachable" (geography is the SOLE blocker)
    if (
        base == "skip"
        and watchlist_dream_flag
        and is_dream_company
        and is_leadership_title
        and geography_match == "unreachable"
    ):
        return "watchlist_dream"
    return base


# ------------------------------------------------------------------
# Public entry point
# ------------------------------------------------------------------

def score_combined(
    job: dict,
    prefs: dict,
    *,
    force_semantic: bool = False,
    skip_semantic:  bool = False,
) -> dict:
    """Run prefilter + Haiku and return the response shape.

    Args:
        job:             normalized job row (title/company/location/
                         description/salary_min/salary_max/remote).
        prefs:           user prefs dict (currently unused by the
                         prefilter but kept for future knobs).
        force_semantic:  if True, ignore cache and always call Haiku.
                         Used by RescoreFn after a candidate_profile edit.
        skip_semantic:   if True, never call Haiku. Used by --skip-semantic
                         in CLI/dry runs and by tests. Job lands in
                         needs_review (or skip if prefilter fails).

    Always returns a dict; never raises. On any internal failure we
    degrade gracefully and surface state via `semantic_skipped`,
    `semantic_api_failed`, and the `tier` value.
    """

    # ---------- 1. Run the binary prefilter ----------------------
    # This replaces the old weighted-score engine.score call. The
    # prefilter decides pass/fail categorically — no 0-100 number.
    pf = run_prefilter(job, prefs)
    passed   = bool(pf.get("passed"))
    track    = pf.get("track") or "TRACK_1_FULLTIME"

    # ---------- 2. Taxonomy + QoL + engagement (orthogonal) ------
    # These run on every job regardless of prefilter outcome — they're
    # cheap, depend only on text + already-scraped fields, and the UI
    # binds filter chips + the QoL sort directly to them. Wrapped in
    # try so a malformed yaml row never aborts a scoring pass.
    try:
        tax = classify_taxonomy(job)
    except Exception:
        tax = {"industries": , "role_types": , "company_group": None}
    try:
        qol = score_qol(job)
    except Exception:
        qol = {"score": 0, "breakdown": {}}
    try:
        engagement_type = detect_engagement(job)
    except Exception:
        engagement_type = "unclear"

    # ---------- 3. Build the base response shape -----------------
    # Populated from the prefilter verdict + orthogonal layers. We
    # over-populate here and then mutate score/tier/semantic_* below
    # depending on whether we call Haiku.
    out: dict[str, Any] = {
        # Score + tier — filled in at the end.
        # BUG 3 — default tier is now DISQUALIFIED rather
        # than "skip". A row only escapes this default by either:
        #   (a) prefilter passing AND Haiku returning a score >= 35, or
        #   (b) prefilter passing AND Haiku unavailable (-> needs_review)
        # Anything else (prefilter fail, prefilter pass + low Haiku score)
        # ends up tier=DISQUALIFIED or tier=skip respectively.
        "score":               0,
        "tier":                "DISQUALIFIED",
        "track":               track,
        # Legacy shape bits kept for downstream consumers.
        "breakdown":           {"prefilter": pf},
        "gates_triggered":     list(pf.get("hard_disqualifiers") or ),
        "modifiers_applied":   list(pf.get("soft_warnings") or ),
        "algo_score":          100 if passed else 0,  # binary diagnostic
        # Semantic fields — default to "never called".
        "semantic_score":      None,
        "semantic_rationale":  None,
        "semantic_scored_at":  None,
        "semantic_model":      None,
        "semantic_skipped":    True,
        # semantic_api_failed: distinguishes "we tried Haiku but it
        # errored / 429'd / returned junk" (True) from "we never tried"
        # (False). Used by rescore.py to skip overwriting a
        # previously-good row with a degraded response on a transient
        # API failure.
        "semantic_api_failed": False,
        "work_mode":           None,
        # Orthogonal layers.
        "industries":          tax["industries"],
        "role_types":          tax["role_types"],
        "company_group":       tax["company_group"],
        "qol_score":           qol["score"],
        "qol_breakdown":       qol["breakdown"],
        "engagement_type":     engagement_type,
        # Prefilter passthroughs — exposed on the row so
        # the detail view + export scripts can explain WHY we did/didn't
        # call Haiku. `passed_prefilter` duplicates pf["passed"] so the
        # frontend doesn't have to dig into breakdown.prefilter.
        "passed_prefilter":    passed,
        # BUG 3 — guarantee a non-empty prefilter_reason.
        # The prefilter itself now returns "unknown_prefilter_fail" for
        # the no-specific-reason case; this defaults to "passed" for
        # rows that genuinely passed (no fail recorded).
        "prefilter_reason":    pf.get("prefilter_reason") or (
            "passed" if passed else "unknown_prefilter_fail"
        ),
        "hard_disqualifiers":  list(pf.get("hard_disqualifiers") or ),
        "soft_warnings":       list(pf.get("soft_warnings") or ),
        "positive_signals":    list(pf.get("positive_signals") or ),
        "is_dream_company":    bool(pf.get("is_dream_company") or False),
        "is_hrc100":           bool(pf.get("is_hrc100") or False),
        "is_crunch_co":        bool(pf.get("is_crunch_co") or False),
        # round-trip the new prefilter is_leadership_title
        # flag for downstream consumers (combined uses it for watchlist
        # gating; the export script uses it as a diagnostic chip).
        "is_leadership_title": bool(pf.get("is_leadership_title") or False),
        "location_flag":       pf.get("location_flag"),
        "industry":            pf.get("industry"),
        "industry_score":      pf.get("industry_score"),
        # Haiku structured passthroughs — default to "unclear" / False
        # / . Overwritten when Haiku returns a full response.
        "role_family_match":   "unclear",
        "industry_match":      "unclear",
        "geography_match":     "unclear",
        "level_match":         "unclear",
        "watchlist_dream":     False,
        "life_fit_concerns":   ,
    }

    # ---------- 4. Prefilter fail → final score 0, tier DISQUALIFIED ----
    # No Haiku call. This is the binary-gate case: hard disqualifier
    # (wrong function / sub-VP seniority / unpaid engagement / etc.).
    # BUG 3 — final_score MUST be 0 (no blended leak from
    # algo_score), tier MUST be "DISQUALIFIED" (not the empty / skip
    # values the original author's audit found on 3,396 rows).
    if not passed:
        out["score"] = 0
        out["tier"]  = "DISQUALIFIED"
        return out

    # ---------- 5. Should we skip Haiku anyway? -------------------
    # Kill switch in scoring.yaml or caller-level skip_semantic
    # (dry run, unit tests). Prefilter-passed jobs with no semantic
    # land in needs_review so the original author sees them in the UI rather than
    # silently falling into skip.
    if skip_semantic or not _enabled:
        out["tier"] = _derive_tier(
            passed_prefilter=True,
            semantic_available=False,
            final_score=0,
            watchlist_dream_flag=False,
            is_dream_company=bool(pf.get("is_dream_company") or False),
            is_leadership_title=bool(pf.get("is_leadership_title") or False),
            geography_match="unclear",
        )
        return out

    # ---------- 6. Try cache first, else call Haiku ---------------
    semantic_payload: Optional[dict] = None
    if not force_semantic:
        semantic_payload = _read_cached_semantic(job)

    if semantic_payload is None:
        semantic_payload = call_semantic(job, prefilter_output=pf)
        # Live call attempted. If None, the call failed (429, timeout,
        # unparseable, missing SDK, mid-run kill-switch flip). Flag it
        # so rescore.py can decline to overwrite with a degraded row.
        if semantic_payload is None:
            out["semantic_api_failed"] = True
            out["tier"] = _derive_tier(
                passed_prefilter=True,
                semantic_available=False,
                final_score=0,
                watchlist_dream_flag=False,
                is_dream_company=bool(pf.get("is_dream_company") or False),
                is_leadership_title=bool(pf.get("is_leadership_title") or False),
                geography_match="unclear",
            )
            return out

    # ---------- 7. Wire the semantic result into the output -------
    semantic_int = int(semantic_payload.get("score") or 0)
    watchlist_dream_flag = bool(semantic_payload.get("watchlist_dream") or False)

    out["score"]              = semantic_int
    out["semantic_score"]     = semantic_int
    out["semantic_rationale"] = semantic_payload.get("rationale") or ""
    out["semantic_scored_at"] = semantic_payload.get("scored_at") or ""
    out["semantic_model"]     = semantic_payload.get("model") or ""
    out["semantic_skipped"]   = False
    out["work_mode"]          = semantic_payload.get("work_mode")

    # structured-output passthroughs.
    out["role_family_match"]  = semantic_payload.get("role_family_match") or "unclear"
    out["industry_match"]     = semantic_payload.get("industry_match") or "unclear"
    out["geography_match"]    = semantic_payload.get("geography_match") or "unclear"
    out["level_match"]        = semantic_payload.get("level_match") or "unclear"
    out["watchlist_dream"]    = watchlist_dream_flag
    out["life_fit_concerns"]  = list(semantic_payload.get("life_fit_concerns") or )

    # ---------- 8. Derive tier from the final score ---------------
    # BUG 4 — pass the new is_dream_company /
    # is_leadership_title / geography_match gates into _derive_tier so
    # the watchlist_dream tier only fires for genuine
    # leadership-at-dream-co-blocked-only-by-geography rows.
    out["tier"] = _derive_tier(
        passed_prefilter=True,
        semantic_available=True,
        final_score=semantic_int,
        watchlist_dream_flag=watchlist_dream_flag,
        is_dream_company=bool(pf.get("is_dream_company") or False),
        is_leadership_title=bool(pf.get("is_leadership_title") or False),
        geography_match=str(out.get("geography_match") or "unclear"),
    )

    return out
