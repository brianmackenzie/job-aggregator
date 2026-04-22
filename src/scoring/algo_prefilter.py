"""
# PERSONAL PROFILE DATA — REPLACE BEFORE USING AT SCALE
#
# This module contains constants that encode the ORIGINAL AUTHOR'S
# personal job-search profile (geography, target companies, target
# keywords, career-history heuristics). Shipping these as-is in a
# public fork is safe (the data is not secret) but the scoring
# behavior will be tuned for the original author, not you.
#
# For a proper fork:
#   1. Edit `config/candidate_profile.yaml` first — it drives the
#      Claude Haiku semantic layer, which is the dominant signal.
#   2. Come back here and rewrite the constants below to match
#      your own geography, industry keywords, and company lists.
#
# See `docs/FORKING.md` for a file-by-file guide.
"""

"""
src/scoring/algo_prefilter.py — Binary prefilter.

The single public entry point:

    prefilter(job: dict, prefs: dict) -> dict

Returns a "fit verdict" dict. The prefilter DOES NOT PRODUCE A SCORE. It
makes a binary pass/fail decision, lists all the flag categories that
fired, and gives combined.py everything it needs to (a) decide whether
to call Haiku, (b) populate the Haiku prompt, and (c) feed the UI /
markdown export.

Output shape:

    {
      "passed":               bool,    # True = call Haiku; False = skip
      "prefilter_reason":     str,     # short tag for why it failed
      "hard_disqualifiers":   list[str],   # all disqualifier tags that fired
      "soft_warnings":        list[str],   # tags to pass to Haiku
      "positive_signals":     list[str],   # POSITIVE_SIGNALS categories hit
      "industry":             str,     # bucket name or "unknown"
      "industry_score":       int,     # 0-10, diagnostic
      "track":                str,     # TRACK_1_FULLTIME / TRACK_2_INTERIM / TRACK_3_PIVOT
      "location_flag":        str,     # remote_us / nyc_metro / nj_office / out_of_area / heavy_office / unknown
      "is_dream_company":     bool,    # in INDUSTRY_SCORES >= 9 via COMPANY_INDUSTRY_MAP
      "is_hrc100":            bool,    # HRC Corporate Equality 100 company
      "is_crunch_co":         bool,    # known crunch-culture studio
      "company_normalized":   str,     # echo input
    }

Design rules:
  - NO weighted math. NO score. That's semantic.py's job now.
  - The prefilter either kills the job or lets it through. It does not
    "penalize" anything. Penalties (gray-area signals) become
    `soft_warnings` that Haiku weighs.
  - Prefilter CANNOT override Haiku — if prefilter says passed=True,
    combined.py calls Haiku and takes Haiku's final score.
  - Prefilter MUST be deterministic and side-effect-free. No I/O, no
    randomness, no network calls.
  - All inputs come from `candidate_profile.py` (Python constants, not
    YAML). The YAML file is now for the diagnostic weighted algo_score
    only (computed by the legacy engine.py when requested).

Called by: src/scoring/combined.py::score_combined (after rewrite).
"""
from __future__ import annotations

import re
from typing import Any

from . import candidate_profile as CP


# ---------------------------------------------------------------------------
# Pre-compiled regex patterns (done at import time for perf).
# ---------------------------------------------------------------------------

# Word-boundary regex for the exec acronyms (CTO/COO/CPO). Avoids matching
# "cto" inside "direCTOr".
_ACRONYM_EXCEPTION_RE = re.compile(
    r"\b(?:" + "|".join(CP.LEADERSHIP_ACRONYM_EXCEPTIONS) + r")\b",
    re.IGNORECASE,
)

# compile the new LEADERSHIP_WHITELIST_PATTERNS into a single
# OR'd regex. Used to short-circuit the wrong-function gate when a title
# clearly carries an executive / leadership role-noun.
_LEADERSHIP_WHITELIST_RE = re.compile(
    "(?:" + "|".join(CP.LEADERSHIP_WHITELIST_PATTERNS) + ")",
    re.IGNORECASE,
)

# compile a SINGLE word-boundary regex that matches any
# HARD_DISQUALIFIER_TITLES_FUNCTION entry as a standalone phrase. The
# old code used naive `kw in title_lo` substring matching, which killed
# titles like "Senior Engineering Manager" because "senior engineer" is
# a literal substring. With \b...\b, "senior engineer" requires a word
# boundary at the end — which is NOT present before "ing", so
# "engineering" no longer matches.
#
# Sort phrases longest-first so the regex prefers the most specific
# match (e.g., "machine learning engineer" is preferred over "ml
# engineer"). Phrases are stripped of leading/trailing whitespace and
# escaped for regex. Phrases <2 chars are dropped defensively.
def _build_function_disqualifier_regex -> re.Pattern[str]:
    """Build the single OR'd regex for HARD_DISQUALIFIER_TITLES_FUNCTION."""
    seen: set[str] = set
    cleaned: list[str] = 
    for raw in CP.HARD_DISQUALIFIER_TITLES_FUNCTION:
        kw = raw.strip
        if len(kw) < 2 or kw in seen:
            continue
        seen.add(kw)
        cleaned.append(kw)
    # Longest-first ensures regex alternation matches the most specific
    # phrase rather than a contained shorter one.
    cleaned.sort(key=len, reverse=True)
    pattern = r"\b(?:" + "|".join(re.escape(p) for p in cleaned) + r")\b"
    return re.compile(pattern, re.IGNORECASE)


_FUNCTION_DISQUALIFIER_RE = _build_function_disqualifier_regex


# Compile the location regex groups once. Cache as dict[str, list[re.Pattern]].
_LOC_RE: dict[str, list[re.Pattern[str]]] = {
    flag: [re.compile(p) for p in patterns]
    for flag, patterns in CP.LOCATION_PATTERNS.items
}


# ---------------------------------------------------------------------------
# Helpers — shared by multiple sub-checks.
# ---------------------------------------------------------------------------

def _title_lo(job: dict) -> str:
    """Lowercase job title, empty string on missing/None."""
    return (job.get("title") or "").lower


def _full_text_lo(job: dict) -> str:
    """Lowercase concatenation of title + company + description.

    Used for positive-signal, soft-warning, and industry-keyword matching
    where context matters beyond just the title. Mirrors the behavior of
    the legacy keywords.build_text helper.
    """
    parts = [
        job.get("title", "") or "",
        job.get("company", "") or "",
        job.get("description", "") or "",
    ]
    return " ".join(parts).lower


def _normalize_company(job: dict) -> str:
    """Return job['company_normalized'] if set, else lowercase-trim company."""
    if job.get("company_normalized"):
        return str(job["company_normalized"]).strip.lower
    return (job.get("company") or "").strip.lower


# ---------------------------------------------------------------------------
# Step 1: Hard disqualifier — WRONG_FUNCTION_TITLES
#
# Mirrors the four-step logic that lived in gates.py::check_function:
#
#   (a) Priority disqualifier match → KILL
#   (b) Leadership exception match (and not diluted) → EXEMPT
#   (c) Leadership acronym exception match → EXEMPT
#   (d) Regular wrong-function kw match → KILL
#   (e) Diluted leadership exception (was in list but had diluting prefix,
#       and no other kw fired) → KILL
# ---------------------------------------------------------------------------

def _exception_is_diluted(title_lo: str, exception_kw: str) -> bool:
    """True if a leadership-exception match is preceded by a downgrading
    prefix (associate / junior / 3d / visual / etc.).

    See candidate_profile.DILUTING_PREFIXES for the full list. Example:
    "Associate 3D Design Director" contains "design director" (exec
    exception), but "associate" and "3d" both dilute it → exception
    INVALIDATED, title falls through to the regular gate check.
    """
    idx = title_lo.find(exception_kw)
    if idx <= 0:
        return False  # exception is at the start of the title, nothing to dilute
    head = title_lo[:idx]
    return any(d in head for d in CP.DILUTING_PREFIXES)


def _check_wrong_function(title_lo: str) -> str | None:
    """Return the matched disqualifier kw (or None if title passes).

    rewrite. The order:

      (a) Priority disqualifier (D2C / ads / performance marketing) →
          KILL even if a leadership token is present. These are the
          lanes the original author is explicitly walking away from regardless of
          seniority.

      (b) Look for ANY word-boundary match in
          HARD_DISQUALIFIER_TITLES_FUNCTION. If none, the title passes.

      (c) A disqualifier WAS matched. Now check exemptions:
            (c-i)  LEADERSHIP_WHITELIST_PATTERNS  → EXEMPT
            (c-ii) Legacy LEADERSHIP_EXCEPTIONS substring (non-diluted)
                                                  → EXEMPT
            (c-iii) Legacy LEADERSHIP_ACRONYM_EXCEPTIONS (\bcto\b /
                    \bcoo\b / \bcpo\b)            → EXEMPT
            (c-iv) Diluted leadership exception (e.g., "Associate 3D
                    Design Director") → KILL with diluted_exception tag

      (d) No exemption fired → KILL with the matched keyword.

    Matches against TITLE ONLY (description content is too noisy).
    """
    # (a) Priority disqualifier — always kills, even with an exec prefix.
    for kw in CP.PRIORITY_DISQUALIFIERS:
        if kw in title_lo:
            return f"priority_disqualifier:{kw}"

    # (b) Single-pass word-boundary search across the disqualifier set.
    m = _FUNCTION_DISQUALIFIER_RE.search(title_lo)
    if not m:
        # No disqualifier hit — title clearly passes the wrong-function gate.
        return None
    matched_kw = m.group(0)

    # (c-i) NEW — leadership whitelist short-circuit. If the
    # title clearly carries an executive role-noun (VP / Director /
    # Engineering Manager / VP Analyst / etc.), the wrong-function gate
    # is suppressed and Haiku gets to weigh the role.
    if _LEADERSHIP_WHITELIST_RE.search(title_lo):
        return None

    # (c-ii) Legacy substring leadership exception (non-diluted). Kept
    # for backward compatibility — the new regex whitelist covers most of
    # these but we keep the old list to honor explicit per-phrase intent.
    for exc in CP.LEADERSHIP_EXCEPTIONS:
        if exc in title_lo and not _exception_is_diluted(title_lo, exc):
            return None

    # (c-iii) Acronym leadership exception (\bcto\b etc.).
    if _ACRONYM_EXCEPTION_RE.search(title_lo):
        return None

    # (c-iv) Diluted exception sweep — kept from . Fires when the
    # only "leadership" signal was a phrase preceded by a diluting prefix
    # like "Associate 3D Design Director".
    for exc in CP.LEADERSHIP_EXCEPTIONS:
        if exc in title_lo and _exception_is_diluted(title_lo, exc):
            return f"diluted_exception:{exc}"

    # (d) No exemption fired — kill with the matched keyword.
    return f"wrong_function:{matched_kw}"


# ---------------------------------------------------------------------------
# Step 2: Hard disqualifier — SUB_VP_SENIORITY
# ---------------------------------------------------------------------------

def _check_sub_vp_seniority(title_lo: str) -> str | None:
    """Return matched seniority kw (or None) — intern/entry/junior titles."""
    for kw in CP.HARD_DISQUALIFIER_TITLES_SENIORITY:
        if kw in title_lo:
            return f"sub_vp_seniority:{kw.strip}"
    return None


# ---------------------------------------------------------------------------
# Step 3: Hard disqualifier — UNPAID_ENGAGEMENT (title + description)
# ---------------------------------------------------------------------------

def _check_unpaid_engagement(text_lo: str) -> str | None:
    """Commission-only / unpaid / equity-only. Full-text check (not title)."""
    for kw in CP.HARD_DISQUALIFIER_ENGAGEMENT:
        if kw in text_lo:
            return f"unpaid_engagement:{kw}"
    return None


# ---------------------------------------------------------------------------
# Step 4: Industry detection (from candidate_profile, not YAML).
# ---------------------------------------------------------------------------

def _detect_industry(company_normalized: str, text_lo: str) -> str:
    """Return the industry bucket name, or "unknown" if no match.

    Priority:
      1. Exact match in COMPANY_INDUSTRY_MAP (using normalized company name).
      2. First keyword match in INDUSTRY_KEYWORDS (order matters — first
         wins, matching the YAML fallback semantics).
    """
    # 1. Direct company lookup
    if company_normalized in CP.COMPANY_INDUSTRY_MAP:
        return CP.COMPANY_INDUSTRY_MAP[company_normalized]

    # 2. Keyword fallback
    for bucket, kws in CP.INDUSTRY_KEYWORDS.items:
        if any(kw in text_lo for kw in kws):
            return bucket

    return "unknown"


def _industry_score(bucket: str) -> int:
    """Return 0-10 relative score for the bucket, 0 if unknown."""
    return int(CP.INDUSTRY_SCORES.get(bucket, 0))


# ---------------------------------------------------------------------------
# Step 5: Location flag detection (regex).
# ---------------------------------------------------------------------------

def _detect_location(job: dict) -> str:
    """Return a single location-category tag.

    Order of precedence matters — once we match, we return. The order is:
      1. heavy_office (5-day RTO etc.)    → deal-breaker signal
      2. out_of_area (Seattle/SF/LA/etc.) → deal-breaker signal
      3. nj_office                        → best case (commutable)
      4. nyc_metro                        → good (hybrid-commutable)
      5. remote_us                        → good (no commute needed)
      6. unknown                          → default
    """
    # Concatenate location field + title + description. Location field is
    # most reliable; other fields catch the common "NYC — Hybrid 3 days"
    # phrasing that appears in descriptions.
    loc_text = " ".join(
        str(job.get(f) or "") for f in ("location", "title", "description")
    )

    # 1. Heavy office (RTO mandate)
    for pat in _LOC_RE["heavy_office"]:
        if pat.search(loc_text):
            return "heavy_office"

    # 2. Out-of-area (Seattle/SF/etc. without remote)
    for pat in _LOC_RE["out_of_area"]:
        if pat.search(loc_text):
            return "out_of_area"

    # 3. NJ office (commutable from Mountain Lakes)
    for pat in _LOC_RE["nj_office"]:
        if pat.search(loc_text):
            return "nj_office"

    # 4. NYC metro (hybrid etc.)
    for pat in _LOC_RE["nyc_metro"]:
        if pat.search(loc_text):
            return "nyc_metro"

    # 5. US remote
    for pat in _LOC_RE["remote_us"]:
        if pat.search(loc_text):
            return "remote_us"

    return "unknown"


# ---------------------------------------------------------------------------
# Step 6: Positive-signal categorization.
# ---------------------------------------------------------------------------

def _match_positive_signals(text_lo: str) -> list[str]:
    """Return the list of POSITIVE_SIGNALS keys whose keyword lists hit text_lo.

    No scoring — just which categories fired. Used for Haiku prompt
    context, diagnostic flag display, and UI chips.
    """
    matched = 
    for category, kws in CP.POSITIVE_SIGNALS.items:
        if any(kw in text_lo for kw in kws):
            matched.append(category)
    return matched


# ---------------------------------------------------------------------------
# Step 7: Soft-warning categorization.
# ---------------------------------------------------------------------------

def _match_soft_warnings(title_lo: str, text_lo: str,
                         location_flag: str) -> list[str]:
    """Return list of soft-warning tags that fired.

    These DO NOT kill the job. They are surfaced to Haiku so the LLM can
    weigh them against role context. Categories:

      - temp_contract        : title looks like staff-aug contractor
      - d2c_in_title         : D2C/commerce/ads phrases in the title
      - below_vp             : "Manager" / "Sr Manager" / "Associate" level
      - crunch_language      : fast-paced / hustle / relentless phrasing
      - culture_red_flags    : "work hard play hard" / "rockstar" phrasing
      - high_travel          : 50%+ travel
      - rto_mandate          : 5-day in-office (also flagged by location)
      - hands_on_coding      : "write production code" / "pair programming"
      - crunch_company       : company is in CRUNCH_COMPANIES list
    """
    warnings = 

    if any(kw in title_lo for kw in CP.SOFT_WARNING_TEMP_CONTRACT):
        warnings.append("temp_contract")

    if any(kw in title_lo for kw in CP.SOFT_WARNING_D2C_IN_TITLE):
        warnings.append("d2c_in_title")

    # TPM titles. Used to be a hard kill but the original author's audit
    # found senior-TPM at gaming dream-cos being false-killed; now it's a
    # soft warning so Haiku can weigh the JD specifics.
    if any(kw in title_lo for kw in CP.SOFT_WARNING_TPM):
        warnings.append("tpm_title")

    # "Below VP" only fires if we didn't also see a VP+ signal.
    # "VP of X" contains "VP" but doesn't contain any below-VP kw, so this
    # just guards against titles like "Senior Manager, Strategy" at BigCo
    # getting flagged alongside a real exec title.
    has_vp_signal = any(
        kw in title_lo for kw in CP.POSITIVE_SIGNALS["senior_titles"]
        if kw not in ("principal", "director")  # too weak to override alone
    )
    if not has_vp_signal:
        if any(kw in title_lo for kw in CP.SOFT_WARNING_BELOW_VP):
            warnings.append("below_vp")

    if any(kw in text_lo for kw in CP.SOFT_WARNING_CRUNCH_PHRASES):
        warnings.append("crunch_language")

    if any(kw in text_lo for kw in CP.SOFT_WARNING_CULTURE_REDFLAGS):
        warnings.append("culture_red_flags")

    # Travel — regex-based %-threshold check. >=50% is a soft warn.
    tm = re.search(r"travel\s*(up to|:)?\s*(\d{2,3})\s*%", text_lo)
    if tm:
        try:
            pct = int(tm.group(2))
            if pct >= 50:
                warnings.append("high_travel")
        except (ValueError, IndexError):
            pass

    # RTO phrases OR the location classified as heavy_office.
    if location_flag == "heavy_office" or any(
        kw in text_lo for kw in CP.SOFT_WARNING_RTO_MANDATE
    ):
        warnings.append("rto_mandate")

    if any(kw in text_lo for kw in CP.SOFT_WARNING_HANDS_ON_CODING):
        warnings.append("hands_on_coding")

    return warnings


# ---------------------------------------------------------------------------
# Step 8: Track routing.
# ---------------------------------------------------------------------------

def _detect_track(positive_signals: list[str], industry: str) -> str:
    """Determine the original author's three-track model assignment.

    Priority (first match wins):
      TRACK_2_INTERIM  — interim/fractional signals present
      TRACK_3_PIVOT    — industry in the pivot list
      TRACK_1_FULLTIME — default (everything else)

    Note the priority matters — a role can be BOTH interim AND at a
    dream-pivot company (e.g. "Fractional CTO at Spotify"), and in that
    case TRACK_2 wins because engagement shape is the more binding
    constraint for scheduling.
    """
    if "interim" in positive_signals:
        return "TRACK_2_INTERIM"
    if industry in CP.TRACK_3_PIVOT_INDUSTRIES:
        return "TRACK_3_PIVOT"
    return "TRACK_1_FULLTIME"


# ---------------------------------------------------------------------------
# Step 9: "Dream company" flag.
# ---------------------------------------------------------------------------

def _is_dream_company(company_normalized: str, industry_score: int) -> bool:
    """True if company is in COMPANY_INDUSTRY_MAP AND industry score >= 9.

    This matches the original author's explicit dream-tier: gaming_publisher_platform,
    digital_tcg_ccg, immersive_lbe, gaming_b2b_infrastructure (all 9+).
    Used for the WATCHLIST_DREAM tier in combined.py — even if Haiku
    scores the specific role low, dream-company roles get surfaced for
    the original author's awareness.
    """
    return (
        company_normalized in CP.COMPANY_INDUSTRY_MAP
        and industry_score >= 9
    )


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------

def prefilter(job: dict, prefs: dict | None = None) -> dict[str, Any]:
    """Binary prefilter — does this job merit a Haiku call?

    Args:
        job:    Job dict (title, company, description, location, etc.).
                All fields are treated as optional; missing fields are
                replaced with empty strings.
        prefs:  User preferences (currently unused; wired in for 
                personalization hooks).

    Returns:
        Verdict dict. See module docstring for the full shape.

    Semantics:
      - passed=True means: no hard disqualifier fired. combined.py should
        call Haiku. The role is a candidate for scoring.
      - passed=False means: a hard disqualifier fired. combined.py MUST
        NOT call Haiku. The role gets final_score=0 and is tagged with
        prefilter_reason in DynamoDB for debuggability.

    The prefilter is intentionally conservative — if we're not sure,
    let Haiku decide. Soft flags (below-VP title, d2c-in-title, crunch
    language) become `soft_warnings` rather than disqualifiers.
    """
    prefs = prefs or {}

    # Short-circuit-friendly helpers
    title_lo = _title_lo(job)
    text_lo  = _full_text_lo(job)
    company_normalized = _normalize_company(job)

    # ---- Hard disqualifiers (any one → passed=False) ----
    hard_disqualifiers: list[str] = 
    first_reason: str | None = None

    # (1) Wrong function — IC / wrong-lane title
    fn_reason = _check_wrong_function(title_lo)
    if fn_reason:
        hard_disqualifiers.append(fn_reason)
        first_reason = first_reason or fn_reason

    # (2) Sub-VP seniority
    sn_reason = _check_sub_vp_seniority(title_lo)
    if sn_reason:
        hard_disqualifiers.append(sn_reason)
        first_reason = first_reason or sn_reason

    # (3) Unpaid / commission-only engagement
    eng_reason = _check_unpaid_engagement(text_lo)
    if eng_reason:
        hard_disqualifiers.append(eng_reason)
        first_reason = first_reason or eng_reason

    # BUG 3 sentinel. If the verdict is "fail" but no
    # specific reason was captured (defensive — should not happen in
    # practice, but the original author's audit found 3,396 rows with empty
    # prefilter_reason), tag as "unknown_prefilter_fail" so the export
    # can group them under a known sub_group rather than dropping into
    # an "unspecified" bucket.
    if hard_disqualifiers and not first_reason:
        first_reason = "unknown_prefilter_fail"
        hard_disqualifiers.append("unknown_prefilter_fail")

    # ---- Flag enrichment (always computed for UI + Haiku prompt, even if
    # prefilter failed — we still want to show "why skipped + other flags"
    # in the export). ----
    industry       = _detect_industry(company_normalized, text_lo)
    industry_score = _industry_score(industry)
    location_flag  = _detect_location(job)
    positives      = _match_positive_signals(text_lo)
    softs          = _match_soft_warnings(title_lo, text_lo, location_flag)
    track          = _detect_track(positives, industry)

    # surface a boolean is_leadership_title for downstream
    # use (combined.py uses it to gate watchlist_dream so dream-co
    # rescue only fires for leadership-shaped titles, not for IC roles
    # at dream cos).
    is_leadership_title = bool(_LEADERSHIP_WHITELIST_RE.search(title_lo))

    return {
        "passed":              not hard_disqualifiers,
        "prefilter_reason":    first_reason or "passed",
        "hard_disqualifiers":  hard_disqualifiers,
        "soft_warnings":       softs,
        "positive_signals":    positives,
        "industry":            industry,
        "industry_score":      industry_score,
        "track":               track,
        "location_flag":       location_flag,
        "is_dream_company":    _is_dream_company(company_normalized, industry_score),
        "is_hrc100":           company_normalized in CP.HRC100_COMPANIES,
        "is_crunch_co":        (
            company_normalized in CP.CRUNCH_COMPANIES
            and company_normalized not in CP.CRUNCH_REDUCED_PENALTY_COMPANIES
        ),
        "is_leadership_title": is_leadership_title,
        "company_normalized":  company_normalized,
    }
