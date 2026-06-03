"""taxonomy — deterministic industry / role_type / company_group classifier.

Phase R1 of the redesign. Reads `config/taxonomy.yaml` once at import
time (cached for warm-Lambda re-use) and exposes one public function:

    classify(job: dict) -> dict
        ── Returns:
           {
             "industries":     list[str],   # multi-value, ordered by config
             "role_types":     list[str],   # multi-value, ordered by config
             "company_group":  Optional[str],  # single value (first match wins)
           }

Design notes
------------
* All matching is case-insensitive substring (same approach as
  scoring/keywords.py).  Faster than regex, robust enough for the
  job-text we see.
* `industries` is multi-value because real roles span sectors:
  Roblox is gaming AND tech.  The frontend uses these as filter
  chips with OR-within-category, AND-across-category semantics.
* `role_types` is multi-value because titles overlap disciplines:
  "VP Product Strategy" is product_strategy AND general_management.
* `company_group` is single-value because they're curated buckets;
  if a job fits two, the YAML order resolves it.  tier_s/tier_1/
  tier_2 are *injected* at runtime from companies.yaml so we don't
  duplicate the tier list in two places.
* `title_excludes` on a role_type lets us avoid false positives
  (e.g. "VP Software Engineering" matches engineering_leadership
  instead of software_engineering).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml

# We share scoring.yaml's discovery pattern via this loader function so
# adding a new YAML in /config/ doesn't require new boilerplate.
def _find_config(filename: str) -> Path:
    """Locate a YAML across dev + Lambda layouts.

    Candidate order (first match wins):
      1. /opt/<filename>            — Lambda layer (prod, via ConfigLayer)
      2. <repo-root>/config/...     — Local dev + pytest
      3. <src-root>/config/...      — Legacy src/config/ fallback (pre-A2)
      4. /var/task/config/...       — Legacy runtime fallback (pre-A2)
    """
    here = Path(__file__).resolve.parent
    candidates = [
        Path("/opt") / filename,                       # Lambda layer (prod)
        here.parent.parent / "config" / filename,      # repo root (dev)
        here.parent / "config" / filename,             # legacy src/config/
        Path("/var/task/config") / filename,           # legacy Lambda runtime
    ]
    for p in candidates:
        if p.exists:
            return p
    raise FileNotFoundError(
        f"{filename} not found — checked: " + ", ".join(str(c) for c in candidates)
    )


def _load(filename: str) -> dict:
    with open(_find_config(filename), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


# ---------------------------------------------------------------------
# Module-level singletons.  Loaded once per Lambda cold start; warm
# invocations hit pre-parsed dicts.
# ---------------------------------------------------------------------
TAXONOMY: dict = _load("taxonomy.yaml")

INDUSTRIES_CFG:    dict = TAXONOMY.get("industries", {})
ROLE_TYPES_CFG:    dict = TAXONOMY.get("role_types", {})
COMPANY_GROUPS_CFG: dict = TAXONOMY.get("company_groups", {})


# ---------------------------------------------------------------------
# Tier-based company groups derived at runtime from companies.yaml.
# Lazy-loaded the first time classify is called so we don't pay the
# YAML parse cost in tests that don't need them.
# ---------------------------------------------------------------------
_TIER_GROUPS_CACHE: Optional[dict[str, set[str]]] = None


def _tier_groups -> dict[str, set[str]]:
    """Return {'tier_s': {'roblox', ...}, 'tier_1': {...}, 'tier_2': {...}}.

    Pulls from companies.yaml so tier renames stay in one place.  Returns
    empty sets if companies.yaml isn't found (taxonomy still works on
    keyword + company match, just no tier groups).
    """
    global _TIER_GROUPS_CACHE
    if _TIER_GROUPS_CACHE is not None:
        return _TIER_GROUPS_CACHE
    out: dict[str, set[str]] = {"tier_s": set, "tier_1": set, "tier_2": set}
    try:
        cos = _load("companies.yaml").get("companies", ) or 
        for co in cos:
            tier = str(co.get("tier") or "").upper
            n = (co.get("name_normalized") or "").lower.strip
            if not n:
                continue
            if tier == "S":
                out["tier_s"].add(n)
            elif tier == "1":
                out["tier_1"].add(n)
            elif tier == "2":
                out["tier_2"].add(n)
    except FileNotFoundError:
        pass
    _TIER_GROUPS_CACHE = out
    return out


# ---------------------------------------------------------------------
# Helpers — text and field extraction.
# ---------------------------------------------------------------------

def _job_text(job: dict) -> str:
    """Lower-case concatenation of all matchable text fields."""
    parts = [
        job.get("title", ""),
        job.get("company", ""),
        job.get("description", ""),
    ]
    return " ".join(str(p) for p in parts if p).lower


def _job_title(job: dict) -> str:
    """Lower-case title only — used for role_type matching where
    description-level matches over-trigger."""
    return str(job.get("title", "")).lower


def _job_company_norm(job: dict) -> str:
    """Lower-case normalized company name. Falls back to .lower of
    `company` if company_normalized is absent (e.g. tests)."""
    return str(
        job.get("company_normalized") or job.get("company") or ""
    ).lower.strip


def _any_substring(text: str, kws: list) -> bool:
    return any(kw.lower in text for kw in (kws or ))


def _any_company_match(co_norm: str, companies_list: list) -> bool:
    if not co_norm:
        return False
    co_set = {c.lower.strip for c in (companies_list or )}
    return co_norm in co_set


# ---------------------------------------------------------------------
# Industry classifier — multi-value.
# ---------------------------------------------------------------------

def industries_for(job: dict) -> list[str]:
    """Return every industry tag that applies to this job, in YAML order.

    A job matches an industry if EITHER:
      * the company_normalized appears in the industry's `companies:`
        list (most precise; survives noisy descriptions), OR
      * any keyword in `keywords:` appears in title+company+description.

    Returns an empty list if nothing matches; the caller can decide
    whether to default that to ["other"] for surface UX.
    """
    text = _job_text(job)
    co_norm = _job_company_norm(job)
    matches: list[str] = 
    for name, cfg in INDUSTRIES_CFG.items:
        if _any_company_match(co_norm, cfg.get("companies", )):
            matches.append(name)
            continue
        if _any_substring(text, cfg.get("keywords", )):
            matches.append(name)
    return matches


# ---------------------------------------------------------------------
# Role type classifier — multi-value, title-biased.
# ---------------------------------------------------------------------

def role_types_for(job: dict) -> list[str]:
    """Return every role_type tag that applies to this job, in YAML order.

    Match rules per role_type:
      * If any title_keyword appears in the title → match (strongest signal).
      * EXCEPT if any title_excludes term also appears, suppressing the
        match (used to keep "VP Software Engineering" out of the IC
        software_engineering bucket).
      * Else: if any description_keyword appears in the full text → match
        (weaker fallback; descriptions tend to over-cite disciplines).

    The exclude list is a per-role_type list, not a global one — it's
    "do not call this an X if also a Y" for the specific X.
    """
    title = _job_title(job)
    text = _job_text(job)
    matches: list[str] = 

    for name, cfg in ROLE_TYPES_CFG.items:
        title_kws  = cfg.get("title_keywords", ) or 
        excludes   = cfg.get("title_excludes", ) or 
        desc_kws   = cfg.get("description_keywords", ) or 

        title_hit = _any_substring(title, title_kws)
        if title_hit:
            if excludes and _any_substring(title, excludes):
                # The exclude list suppresses the title match. We do NOT
                # then fall through to description-only matching — if the
                # title says "VP Software Engineering", the role really
                # is leadership, not IC, regardless of the JD body.
                continue
            matches.append(name)
            continue

        if desc_kws and _any_substring(text, desc_kws):
            matches.append(name)

    return matches


# ---------------------------------------------------------------------
# Company group classifier — single-value.
# ---------------------------------------------------------------------

def company_group_for(job: dict) -> Optional[str]:
    """Return the first company_group whose company list contains this
    job's company, or None.  YAML key order resolves overlaps.

    Tier groups (tier_s/tier_1/tier_2) are checked first because they're
    the most editorially curated and the original author uses them as the primary
    'apply now' filter; the YAML-defined groups (gaming_aaa, streaming,
    etc.) come second.
    """
    co_norm = _job_company_norm(job)
    if not co_norm:
        return None

    # Tier groups first (derived from companies.yaml).
    tg = _tier_groups
    for group_name in ("tier_s", "tier_1", "tier_2"):
        if co_norm in tg.get(group_name, set):
            return group_name

    # Then YAML-defined affinity groups.
    for name, cfg in COMPANY_GROUPS_CFG.items:
        if _any_company_match(co_norm, cfg.get("companies", )):
            return name

    return None


# ---------------------------------------------------------------------
# Public composite — what combined.py calls per job.
# ---------------------------------------------------------------------

def classify(job: dict) -> dict:
    """Run all three classifiers and return a single dict.

    This is the only function the rest of the codebase needs to import.
    Always returns all three keys; values may be empty list / None.
    """
    return {
        "industries":    industries_for(job),
        "role_types":    role_types_for(job),
        "company_group": company_group_for(job),
    }
