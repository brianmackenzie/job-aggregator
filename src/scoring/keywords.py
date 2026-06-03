"""Keyword loading and matching utilities for the scoring engine.

All KW_* constants are loaded from config/scoring.yaml at module import
time. Changing scoring.yaml and redeploying is the only change needed
to add or remove keywords — no Python edits required.

Key exports:
  CFG             — the full parsed scoring.yaml dict
  WEIGHTS         — category weight dict
  KW_* keyword lists as Python lists of lowercase strings
  INDUSTRY_MAP    — company_normalized -> industry bucket
  INDUSTRY_KW     — keyword fallback dict for industry detection
  INDUSTRY_SCORES — bucket name -> 0-10 score
  HRC100          — set of company_normalized strings (HRC CEI 100)
  CRUNCH_COS      — set of company_normalized strings (crunch reputation)
  CRUNCH_REDUCED  — set with reduced (not full) crunch penalty
  keyword_hits(text, kw_list) -> int   — count substring matches
  any_match(text, kw_list) -> bool     — True if any keyword present
"""
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml


# ---------------------------------------------------------------------------
# Load config once at module import time. Cache it so multiple Lambda
# invocations in the same container reuse the parsed YAML.
# ---------------------------------------------------------------------------

def _find_config -> Path:
    """Locate scoring.yaml across dev + Lambda layouts.

    Candidate order (first match wins):
      1. /opt/scoring.yaml          — Lambda layer (prod, via ConfigLayer)
      2. <repo-root>/config/...     — Local dev + pytest
      3. <src-root>/config/...      — Legacy src/config/ fallback (pre-A2)
      4. /var/task/config/...       — Legacy runtime fallback (pre-A2)
    """
    here = Path(__file__).resolve.parent
    candidates = [
        Path("/opt/scoring.yaml"),                         # Lambda layer (prod)
        here.parent.parent / "config" / "scoring.yaml",    # repo root (dev)
        here.parent / "config" / "scoring.yaml",           # legacy src/config/
        Path("/var/task/config/scoring.yaml"),             # legacy Lambda runtime
    ]
    for p in candidates:
        if p.exists:
            return p
    raise FileNotFoundError(
        "scoring.yaml not found — checked: " + ", ".join(str(c) for c in candidates)
    )


def _load_cfg -> dict:
    with open(_find_config, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# Module-level singleton — loaded once per Lambda cold start.
CFG: dict = _load_cfg

# ------------------------------------------------------------------
# Convenience accessors
# ------------------------------------------------------------------

WEIGHTS: dict = CFG["weights"]

# Pull out all keyword lists, lowercased (YAML already lowercase but
# we guard in case the original author adds a mixed-case keyword while editing).
_KW = CFG["keywords"]

def _kw(name: str) -> list:
    """Return a lowercase list from the keywords section."""
    return [k.lower for k in _KW.get(name, )]


KW_STRATEGY        = _kw("strategy")
KW_PGM_ARCH        = _kw("program_architecture")
KW_MA_INTEGRATION  = _kw("ma_integration")
KW_SENIOR_TITLES   = _kw("senior_titles")
KW_D2C_PAYMENTS    = _kw("d2c_payments")
KW_HANDS_ON_CODE   = _kw("hands_on_coding")
KW_SENIORITY_DISQ  = _kw("seniority_disqualifiers")
KW_HELPING_PEOPLE  = _kw("helping_people")
KW_GAMING_CULTURE  = _kw("gaming_culture")
KW_MUSIC           = _kw("music_connection")
KW_IMMERSIVE       = _kw("immersive_experiential")
KW_CCG             = _kw("ccg_boardgame")
KW_FAMILY_FRIENDLY = _kw("family_friendly")
KW_CRUNCH_PACE     = _kw("crunch_pace")
KW_CULTURE_REDFLAGS = _kw("culture_red_flags")
KW_LGBTQ           = _kw("lgbtq_signals")
KW_INTERIM         = _kw("interim_fractional")
KW_ENG_DISQ        = _kw("engagement_disqualifiers")
KW_NJ_OFFICE       = _kw("nj_office")

# Industry classification
INDUSTRY_SCORES: dict = CFG["industry_buckets"]                     # bucket -> score
INDUSTRY_MAP: dict    = CFG.get("company_industry_map", {})         # co_norm -> bucket
INDUSTRY_KW: dict     = CFG.get("industry_keywords", {})            # bucket -> [kw]

# Static company sets — normalized (lowercase, suffix-stripped).
_STATIC = CFG.get("static_lists", {})
HRC100:         frozenset = frozenset(_STATIC.get("hrc100_companies", ))
CRUNCH_COS:     frozenset = frozenset(_STATIC.get("crunch_companies", ))
CRUNCH_REDUCED: frozenset = frozenset(_STATIC.get("crunch_reduced_penalty_companies", ))

# Tier thresholds
TIER_CFG: dict  = CFG.get("tiers", {})
TRACK_CFG: dict = CFG.get("tracks", {})

# Location scoring and regex config
_LOC = CFG.get("location", {})
LOC_SCORES: dict = _LOC.get("scores", {})

# Pre-compile location regex patterns into lists of compiled Pattern objects.
def _compile(patterns: list) -> list:
    return [re.compile(p) for p in patterns]

LOC_REMOTE_RE:       list = _compile(_LOC.get("remote_patterns", ))
LOC_NYC_RE:          list = _compile(_LOC.get("nyc_patterns", ))
LOC_NJ_RE:           list = _compile(_LOC.get("nj_patterns", ))
LOC_RELOCATION_RE:   list = _compile(_LOC.get("relocation_patterns", ))
LOC_OUT_OF_AREA_RE:  list = _compile(_LOC.get("out_of_area_patterns", ))
LOC_HEAVY_OFFICE_RE: list = _compile(_LOC.get("in_office_heavy_patterns", ))

# Modifier config
MODIFIERS_CFG: dict = CFG.get("modifiers", {})
GATES_CFG:     dict = CFG.get("gates", {})
COMP_THRESHOLDS: dict = _STATIC.get("comp_thresholds", {})

# ------------------------------------------------------------------
# Text-matching helpers
# ------------------------------------------------------------------

def keyword_hits(text: str, kw_list: list) -> int:
    """Count how many keywords from kw_list appear in text (case-insensitive).

    Each keyword counted at most once even if it appears multiple times,
    so a JD that mentions 'esports' ten times still counts as one hit.
    The text is lowercased once and we do substring search — fast enough
    for job description lengths (<10 KB typical).
    """
    lo = text.lower
    return sum(1 for kw in kw_list if kw in lo)


def any_match(text: str, kw_list: list) -> bool:
    """True if any keyword from kw_list appears anywhere in text."""
    lo = text.lower
    return any(kw in lo for kw in kw_list)


def regex_match(text: str, patterns: list) -> bool:
    """True if any compiled regex pattern matches anywhere in text."""
    return any(p.search(text) for p in patterns)


def build_text(job: dict) -> str:
    """Concatenate all searchable text fields from a job dict into one
    lowercase string. Callers should call this once and pass the result
    to all keyword/regex functions to avoid repeated concatenation.
    """
    parts = [
        job.get("title", ""),
        job.get("company", ""),
        job.get("description", ""),
        job.get("location", ""),
    ]
    return " ".join(p for p in parts if p).lower


def detect_industry(company_normalized: str, text: str) -> str:
    """Return the best-matching industry bucket name for a job.

    Resolution order:
      1. Exact lookup in company_industry_map (most precise).
      2. Keyword scan of full job text against industry_keywords (fallback).
      3. "general_enterprise_tech" as catch-all.
    """
    # 1. Company map lookup.
    bucket = INDUSTRY_MAP.get(company_normalized.lower.strip)
    if bucket:
        return bucket
    # 2. Keyword fallback — first bucket whose keywords appear in text wins.
    for bucket_name, kws in INDUSTRY_KW.items:
        if any(kw in text for kw in kws):
            return bucket_name
    # 3. Fallback.
    return "general_enterprise_tech"


def score_for_industry(bucket: str) -> float:
    """Look up the 0-10 score for an industry bucket."""
    return float(INDUSTRY_SCORES.get(bucket, 3))  # default to general_enterprise_tech


def tier_from_score(score: int) -> str:
    """Map a 0-100 final score to a tier label string."""
    thresholds = [
        ("T1",        TIER_CFG.get("T1", {}).get("min_score", 78)),
        ("T2",        TIER_CFG.get("T2", {}).get("min_score", 65)),
        ("T3",        TIER_CFG.get("T3", {}).get("min_score", 50)),
        ("watchlist", TIER_CFG.get("watchlist", {}).get("min_score", 35)),
    ]
    for tier_name, min_s in thresholds:
        if score >= min_s:
            return tier_name
    return "skip"


def detect_track(text: str, industry: str) -> str:
    """Route the job to a track (TRACK_1_FULLTIME / TRACK_2_INTERIM / TRACK_3_PIVOT).

    Track routing is orthogonal to scoring; it determines which dashboard
    column a job appears in. First matching rule wins.
    """
    # Interim / fractional track — triggered by engagement keywords.
    if any_match(text, KW_INTERIM):
        return "TRACK_2_INTERIM"
    # Passion/pivot track — triggered by industry.
    pivot_industries = TRACK_CFG.get("TRACK_3_PIVOT", {}).get("industries", )
    if industry in pivot_industries:
        return "TRACK_3_PIVOT"
    return "TRACK_1_FULLTIME"
