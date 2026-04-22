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

"""Additive modifier stack — bonuses and penalties applied after the
weighted category sum is converted to 0-100.

Each modifier fires at most once and adds/subtracts a fixed delta.
The engine collects the names of fired modifiers so the job detail
page can show the original author exactly why a score landed where it did.

Public function:
  compute_modifiers(job, text, industry, geo_score, cat_scores) ->
      (total_delta: int, modifiers_applied: list[str])
"""
import re
from typing import Optional

from .keywords import (
    CRUNCH_COS,
    CRUNCH_REDUCED,
    HRC100,
    KW_CRUNCH_PACE,
    KW_D2C_PAYMENTS,
    KW_HANDS_ON_CODE,
    KW_IMMERSIVE,
    KW_CCG,
    KW_MA_INTEGRATION,
    KW_NJ_OFFICE,
    MODIFIERS_CFG,
    any_match,
    keyword_hits,
    regex_match,
    LOC_NJ_RE,
    LOC_REMOTE_RE,
)

# Pre-compile the travel-percentage regex once at import time.
_TRAVEL_RE = re.compile(r"travel\s*(up to|:)?\s*(\d{2,3})\s*%", re.IGNORECASE)


def _cfg(name: str) -> dict:
    """Fetch a modifier's config dict; return empty dict if not found."""
    return MODIFIERS_CFG.get(name, {})


def _delta(name: str) -> int:
    """Return the delta (int) for a named modifier; 0 if not configured."""
    return int(_cfg(name).get("delta", 0))


# ---------------------------------------------------------------------------
# Individual modifier checks — each returns (fired: bool, name: str)
# ---------------------------------------------------------------------------

def _mod_company_tier(job: dict) -> tuple[bool, str, int]:
    """Company tier bonus. Tier is pre-fetched from the Companies table
    by the scrape worker and stored in job['company_tier'] (optional).

    Tier values expected: "1", "2", "S" (case-insensitive).
    """
    tier = str(job.get("company_tier", "") or "").upper.strip
    if tier == "S":
        return True, "company_tier_s", _delta("company_tier_s")
    if tier == "1":
        return True, "company_tier_1", _delta("company_tier_1")
    if tier == "2":
        return True, "company_tier_2", _delta("company_tier_2")
    return False, "", 0


def _mod_immersive(text: str, industry: str) -> tuple[bool, str, int]:
    """Bonus for immersive / themed-entertainment / escape-room content
    AT a company actually classified as immersive_lbe.

    keyword-only matching was promoting brand-experience
    agencies, ad-agency "experiential" roles, and any JD that mentioned "VR"
    or "immersive media" up by +8. The gate now also checks the algo industry
    classifier — only roles whose company_industry_map / industry_keywords
    fallback resolved to immersive_lbe receive the bonus. The required
    industries are read from scoring.yaml::modifiers.immersive_themed.
    requires_industry so they can be expanded without a code change.
    """
    target_industries = _cfg("immersive_themed").get("requires_industry", )
    if not target_industries:
        target_industries = ["immersive_lbe"]
    if industry not in target_industries:
        return False, "immersive_themed", 0

    kws = [k.lower for k in _cfg("immersive_themed").get("keywords", )]
    if not kws:
        kws = ["escape room", "immersive", "themed entertainment", "lbe",
               "location-based entertainment"]
    fired = any_match(text, kws)
    return fired, "immersive_themed", _delta("immersive_themed") if fired else 0


def _mod_tcg(text: str) -> tuple[bool, str, int]:
    """Bonus for trading-card-game / CCG content."""
    kws = [k.lower for k in _cfg("tcg_ccg").get("keywords", )]
    if not kws:
        kws = ["trading card game", "tcg", "ccg", "collectible card", "deckbuilding"]
    fired = any_match(text, kws)
    return fired, "tcg_ccg", _delta("tcg_ccg") if fired else 0


def _mod_ma_gaming(text: str, industry: str) -> tuple[bool, str, int]:
    """Bonus for M&A integration signals in a gaming/media industry role."""
    target_industries = _cfg("ma_gaming_media").get("requires_industry", )
    if not target_industries:
        target_industries = [
            "gaming_publisher_platform", "digital_tcg_ccg", "immersive_lbe",
            "music_tech", "streaming_media", "gaming_b2b_infrastructure",
        ]
    ma_hit    = any_match(text, KW_MA_INTEGRATION)
    right_ind = industry in target_industries
    fired = ma_hit and right_ind
    return fired, "ma_gaming_media", _delta("ma_gaming_media") if fired else 0


def _mod_vp_analyst(title: str) -> tuple[bool, str, int]:
    """Bonus when title explicitly contains 'VP Analyst' or 'Principal Analyst'."""
    kws = [k.lower for k in _cfg("vp_analyst").get("title_keywords", )]
    if not kws:
        kws = ["vp analyst", "principal analyst", "vp, analyst"]
    lo_title = title.lower
    fired = any(kw in lo_title for kw in kws)
    return fired, "vp_analyst", _delta("vp_analyst") if fired else 0


def _mod_remote_vp_gaming(job: dict, text: str, industry: str) -> tuple[bool, str, int]:
    """Bonus for a remote VP-level role in the gaming industry."""
    is_remote    = (job.get("remote") is True) or regex_match(text, LOC_REMOTE_RE)
    is_gaming    = industry in ("gaming_publisher_platform", "gaming_b2b_infrastructure",
                                "digital_tcg_ccg")
    lo_title     = (job.get("title") or "").lower
    is_vp        = any(t in lo_title for t in ["vp", "vice president", "head of",
                                                "chief", "director"])
    fired = is_remote and is_gaming and is_vp
    return fired, "remote_vp_gaming", _delta("remote_vp_gaming") if fired else 0


def _mod_nj_office(job: dict, text: str) -> tuple[bool, str, int]:
    """Bonus for NJ-based in-office work (commutable from Mountain Lakes)."""
    fired = regex_match(text, LOC_NJ_RE) or any_match(text, KW_NJ_OFFICE)
    # Don't double-fire if remote — NJ bonus is for in-office commute value.
    if job.get("remote"):
        fired = False
    return fired, "nj_office_bonus", _delta("nj_office_bonus") if fired else 0


def _mod_mental_health(text: str) -> tuple[bool, str, int]:
    """Bonus for mental health or accessibility mission content."""
    kws = [k.lower for k in _cfg("mental_health_accessibility").get("keywords", )]
    if not kws:
        kws = ["mental health", "accessibility", "accessible gaming", "crisis support",
               "suicide prevention", "disability"]
    fired = any_match(text, kws)
    return fired, "mental_health_accessibility", _delta("mental_health_accessibility") if fired else 0


def _mod_multiplayer_live_service(text: str, industry: str) -> tuple[bool, str, int]:
    """Bonus for the original author's RARE specialty: multiplayer infrastructure / live services /
    online platform engineering at scale.

    Per his resume + cover letter: "My specialization is rare: multiplayer
    infrastructure, live-service platform architecture, and the technology
    strategy that turns engineering complexity into competitive advantage at
    massive scale." This was demonstrated at Take-Two / 2K Online Engineering
    (thousands of virtual servers, NBA 2K and WWE 2K matchmaking) and is
    his strongest IC-of-leaders signal.

    Fires when the JD contains 2+ specialty signals AND the role is in a
    gaming-adjacent industry (so we don't accidentally boost an unrelated
    SaaS company that happens to mention "live service").
    """
    target_industries = {
        "gaming_publisher_platform", "gaming_b2b_infrastructure",
        "digital_tcg_ccg", "immersive_lbe", "streaming_media",
    }
    if industry not in target_industries:
        return False, "multiplayer_live_service", 0

    kws = [k.lower for k in _cfg("multiplayer_live_service").get("keywords", )]
    if not kws:
        kws = [
            "multiplayer infrastructure", "multiplayer backend",
            "live service", "live ops", "live operations",
            "online services", "online services platform",
            "matchmaking", "concurrent players",
            "game services", "platform engineering",
            "merchant of record", "subscription platform",
            "post-merger integration", "shared services",
        ]
    threshold = int(_cfg("multiplayer_live_service").get("keyword_count_threshold", 2))
    hits = keyword_hits(text, kws)
    fired = hits >= threshold
    return fired, "multiplayer_live_service", \
        _delta("multiplayer_live_service") if fired else 0


def _mod_interim_advisor_fit(title: str) -> tuple[bool, str, int]:
    """Bonus for legitimate interim / fractional / advisor titles in the original author's lane.

    the original author's three-track positioning (per the generated resume):
      Track 1 — full-time exec roles
      Track 2 — interim CTO engagements
      Track 3 — strategic advisory (gaming, PE, B2B infrastructure)

    This modifier surfaces TRACK 2 + TRACK 3 fits that look like real
    interim/fractional/advisor positions paired with a tech/strategy scope.
    The title must contain BOTH:
      - an engagement marker (interim / fractional / advisor / operating partner)
      - a scope marker that fits the original author (cto / chief technology / chief product /
        chief operating / technology / platform / strategy / product)
    so a "Technical Advisor" IC role doesn't accidentally fire — it requires
    a scope word that matches the original author's lane.
    """
    lo = title.lower

    engagement_markers = [
        "interim", "fractional", "advisor", "advisory",
        "operating partner", "executive in residence", "eir",
    ]
    scope_markers = [
        "cto", "chief technology", "chief product", "chief operating",
        "technology", "platform", "strategy", "product", "transformation",
        "engineering", "operations",
    ]
    if any(m in lo for m in engagement_markers) and any(s in lo for s in scope_markers):
        delta = _delta("interim_advisor_fit")
        return True, "interim_advisor_fit", delta
    return False, "interim_advisor_fit", 0


def _mod_hrc_trans(company_normalized: str, text: str) -> tuple[bool, str, int]:
    """Bonus for HRC-100 company with explicit trans-inclusive healthcare mention."""
    is_hrc = company_normalized in HRC100
    trans_kws = [k.lower for k in _cfg("hrc_trans_inclusive").get("keywords", )]
    if not trans_kws:
        trans_kws = ["transgender-inclusive healthcare", "gender-affirming care",
                     "trans-inclusive", "gender affirming"]
    has_trans = any_match(text, trans_kws)
    fired = is_hrc and has_trans
    return fired, "hrc_trans_inclusive", _delta("hrc_trans_inclusive") if fired else 0


# ---------------------------------------------------------------------------
# Penalty modifiers
# ---------------------------------------------------------------------------

def _mod_d2c_heavy(text: str) -> tuple[bool, str, int]:
    """Penalty when 3+ D2C/payments keywords appear — wrong role type."""
    threshold = int(_cfg("d2c_heavy").get("keyword_count_threshold", 3))
    hits = keyword_hits(text, KW_D2C_PAYMENTS)
    fired = hits >= threshold
    return fired, "d2c_heavy", _delta("d2c_heavy") if fired else 0


def _mod_coding_heavy(text: str) -> tuple[bool, str, int]:
    """Penalty when 3+ hands-on coding keywords appear."""
    threshold = int(_cfg("coding_heavy").get("keyword_count_threshold", 3))
    hits = keyword_hits(text, KW_HANDS_ON_CODE)
    fired = hits >= threshold
    return fired, "coding_heavy", _delta("coding_heavy") if fired else 0


def _mod_crunch_culture(text: str) -> tuple[bool, str, int]:
    """Penalty for 3+ crunch-culture red flags in the JD."""
    threshold = int(_cfg("crunch_culture").get("keyword_count_threshold", 3))
    hits = keyword_hits(text, KW_CRUNCH_PACE)
    fired = hits >= threshold
    return fired, "crunch_culture", _delta("crunch_culture") if fired else 0


def _mod_below_vp(title: str) -> tuple[bool, str, int]:
    """Penalty when the title is Manager / Sr Manager / Associate level.

    the original author targets VP+ roles. A Manager-level role at Roblox (Tier S) shouldn't
    outscore a VP-level role at a less desirable company. The tier bonus can
    inflate Manager-level scores at dream companies; this modifier corrects that.

    Does NOT fire for Director or above (Director is borderline acceptable),
    and does NOT fire when "senior manager" is part of a larger title that
    contains VP/Director (e.g., "VP, Senior Manager" is unusual but possible).
    """
    lo = title.lower.strip

    # Skip if Director+ or VP-level exec-track is in the title.
    # NOTE: we DO NOT skip just because the word "principal" appears —
    # "Principal App Store Manager" at Roblox is still a below-VP role, not
    # an executive. The engine's _leadership_score separately gives Principal
    # IC titles leadership=1 so the role_fit category already scores low.
    if any(t in lo for t in ["director", "vp ", " vp", "vp,", "vice president",
                               "svp", "evp", "chief", "head of", "general manager",
                               "managing director", "fellow", "cto", "cfo", "ceo",
                               "coo", "cmo", "cpo", "cro", "chro", "cio", "ciso"]):
        return False, "below_vp_title", 0

    # Fire for Manager / Senior Manager / Associate / Coordinator / Specialist
    # level titles that survived the function and seniority gates.
    # Patterns include:
    #   - "Senior Manager" / " Manager" (with leading space)
    #   - "Manager, X" (title STARTS with "Manager")
    #   - "Associate X" (title STARTS with "Associate")
    below_vp_patterns = [
        "senior manager", "sr. manager", "sr manager",
        " manager",                        # " Manager" mid-title
        "coordinator", "specialist",
        " lead", "lead ",                  # "Team Lead" / "Lead Generation"
        " associate", "associate ",
    ]
    if any(t in lo for t in below_vp_patterns):
        return True, "below_vp_title", _delta("below_vp_title")
    # Title that STARTS with "Manager," or "Associate " — leading space misses these.
    if lo.startswith("manager,") or lo.startswith("manager ") or \
       lo.startswith("associate ") or lo.startswith("associate,"):
        return True, "below_vp_title", _delta("below_vp_title")

    return False, "below_vp_title", 0


def _mod_d2c_in_title(title: str) -> tuple[bool, str, int]:
    """Heavy penalty when the job TITLE itself contains D2C / Commerce / Payments / Ads.

    Per the real-career-pivot research, the original author is intentionally moving AWAY from
    D2C / direct-to-consumer / digital commerce / ads work (even though his
    resume has some of it). Any title that foregrounds this function should
    take a hard penalty so it can't outscore true strategy/CTO/advisory roles.
    """
    kws = [k.lower for k in _cfg("d2c_in_title").get("title_keywords", )]
    if not kws:
        kws = ["d2c", " commerce", "payments", "payment", "e-commerce", "ecommerce",
               " ads", "advertising", "performance marketing", "growth marketing"]
    lo_title = title.lower
    fired = any(kw in lo_title for kw in kws)
    return fired, "d2c_in_title", _delta("d2c_in_title") if fired else 0


def _mod_temp_contract_title(title: str) -> tuple[bool, str, int]:
    """Penalty when the title contains "temporary" or explicit contract-worker markers.

    the original author wants REAL interim / fractional CTO engagements (strategic advisory),
    NOT staff-augmentation contract roles ("Talent Sourcing Partner Temporary").
    Those contractor titles are often wrong-function AND wrong-level.

    Does NOT fire for "interim", "fractional", "advisor", or "consultant" —
    those are the engagements the original author is actually targeting via TRACK_2_INTERIM.
    """
    lo = title.lower
    # Explicit wrong-shape contractor markers.
    bad_kws = [
        "(temporary)",
        " temporary",
        "temporary)",
        "(contract)",
        "(contractor)",
        "temp-",
        " temp ",
        "short-term",
        "short term",          # no-hyphen variant ("Event Specialist (Short Term)")
        "(short term)",
        "seasonal",
        "part-time",
        "part time",
    ]
    fired = any(kw in lo for kw in bad_kws)
    # But DON'T penalize if it's explicitly a real fractional/interim role.
    if fired and any(good in lo for good in ["interim", "fractional", "advisor",
                                              "consultant", "fractional cto",
                                              "interim cto"]):
        fired = False
    return fired, "temp_contract_title", _delta("temp_contract_title") if fired else 0


def _mod_high_travel(text: str) -> tuple[bool, str, int]:
    """Penalty for travel >50% requirement in the job description."""
    m = _TRAVEL_RE.search(text)
    if m:
        try:
            pct = int(m.group(2))
            if pct >= 50:
                return True, "high_travel", _delta("high_travel")
        except (ValueError, IndexError):
            pass
    return False, "high_travel", 0


def _mod_rto_mandate(text: str) -> tuple[bool, str, int]:
    """Penalty for explicit 5-day in-office RTO mandate."""
    from .keywords import LOC_HEAVY_OFFICE_RE, regex_match as rm
    fired = rm(text, LOC_HEAVY_OFFICE_RE)
    return fired, "rto_mandate", _delta("rto_mandate") if fired else 0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_modifiers(
    job: dict,
    text: str,
    industry: str,
    geo_score: float,
    cat_scores: dict,
) -> tuple[int, list]:
    """Run every modifier check and return the total delta and fired names.

    Args:
        job:        The raw job dict (must include 'title', 'company_normalized',
                    optionally 'company_tier', 'remote').
        text:       Pre-built searchable text (title + company + description + location).
        industry:   Industry bucket string from keywords.detect_industry.
        geo_score:  Geographic category score (0-10) — used for context.
        cat_scores: Dict of all category scores (not currently used but
                    available for future compound modifiers).

    Returns:
        (total_delta: int, modifiers_applied: list[str])
        total_delta is the sum of all fired modifier deltas.
        modifiers_applied lists each fired modifier name.
    """
    title             = job.get("title", "")
    company_normalized = job.get("company_normalized", "")

    fired_names:  list[str] = 
    total_delta:  int       = 0

    # Helper: register a modifier if it fired.
    def _register(fired: bool, name: str, delta: int) -> None:
        if fired and name:
            fired_names.append(name)
            nonlocal total_delta
            total_delta += delta

    # Bonuses
    f, n, d = _mod_company_tier(job);                    _register(f, n, d)
    f, n, d = _mod_immersive(text, industry);            _register(f, n, d)
    f, n, d = _mod_tcg(text);                            _register(f, n, d)
    f, n, d = _mod_ma_gaming(text, industry);            _register(f, n, d)
    f, n, d = _mod_vp_analyst(title);                    _register(f, n, d)
    f, n, d = _mod_remote_vp_gaming(job, text, industry); _register(f, n, d)
    f, n, d = _mod_nj_office(job, text);                 _register(f, n, d)
    f, n, d = _mod_mental_health(text);                  _register(f, n, d)
    f, n, d = _mod_hrc_trans(company_normalized, text);  _register(f, n, d)
    # Resume-tuned bonuses:
    #   - multiplayer_live_service: the original author's rare specialty signal in JD
    #   - interim_advisor_fit:      Track-2/3 engagements (interim/fractional/advisor)
    f, n, d = _mod_multiplayer_live_service(text, industry); _register(f, n, d)
    f, n, d = _mod_interim_advisor_fit(title);               _register(f, n, d)

    # Penalties
    f, n, d = _mod_below_vp(title);                       _register(f, n, d)
    f, n, d = _mod_d2c_in_title(title);                  _register(f, n, d)
    f, n, d = _mod_temp_contract_title(title);           _register(f, n, d)
    f, n, d = _mod_d2c_heavy(text);                      _register(f, n, d)
    f, n, d = _mod_coding_heavy(text);                   _register(f, n, d)
    f, n, d = _mod_crunch_culture(text);                 _register(f, n, d)
    f, n, d = _mod_high_travel(text);                    _register(f, n, d)
    f, n, d = _mod_rto_mandate(text);                    _register(f, n, d)

    return total_delta, fired_names
