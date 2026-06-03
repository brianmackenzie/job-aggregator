"""Scoring engine — the single public function is score(job, prefs).

Formula (per the original author's spec v1.0, section 5.4):
    category_sum = sum(category_score[i] * category_weight[i])   # each 0-10
    raw          = category_sum * geographic_gate * engagement_gate * 10 + modifier_delta
    final        = clamp(round(raw), 0, 100)
    Any hard gate triggered -> final = 0 regardless.

All ten category scorers are private functions in this module. The only
public surface is:
    score(job: dict, prefs: dict) -> dict

Return shape:
    {
      "score":             int 0-100,
      "tier":              str,           # "T1" | "T2" | "T3" | "watchlist" | "skip"
      "track":             str,           # "TRACK_1_FULLTIME" | "TRACK_2_INTERIM" | "TRACK_3_PIVOT"
      "breakdown":         dict,          # category_name -> score (0-10)
      "gates_triggered":   list[str],     # e.g. ["seniority"]
      "modifiers_applied": list[str],     # e.g. ["company_tier_1", "remote_vp_gaming"]
    }
"""
import re
from statistics import mean
from typing import Optional

from .gates import evaluate_all_gates
from .keywords import (
    CFG,
    COMP_THRESHOLDS,
    CRUNCH_COS,
    CRUNCH_REDUCED,
    HRC100,
    KW_CCG,
    KW_CRUNCH_PACE,
    KW_CULTURE_REDFLAGS,
    KW_D2C_PAYMENTS,
    KW_FAMILY_FRIENDLY,
    KW_GAMING_CULTURE,
    KW_HANDS_ON_CODE,
    KW_HELPING_PEOPLE,
    KW_IMMERSIVE,
    KW_INTERIM,
    KW_LGBTQ,
    KW_MA_INTEGRATION,
    KW_MUSIC,
    KW_PGM_ARCH,
    KW_SENIOR_TITLES,
    KW_STRATEGY,
    WEIGHTS,
    any_match,
    build_text,
    detect_industry,
    detect_track,
    keyword_hits,
    score_for_industry,
    tier_from_score,
)
from .modifiers import compute_modifiers


# ---------------------------------------------------------------------------
# Category 1: Role fit (weight 0.22)
# ---------------------------------------------------------------------------

def _score_role_fit(title: str, description: str) -> float:
    """Six sub-factors averaged, each 0-10.

    1. Strategy keyword density
    2. Program / architecture keyword density
    3. M&A integration keyword density
    4. Leadership scope (inferred from title)
    5. D2C / payments avoidance (inverted penalty)
    6. Hands-on coding avoidance (inverted penalty)
    """
    text = f"{title} {description}".lower

    # Sub-factor 1: Strategy signals (+2 per unique keyword hit, cap 10).
    s_strategy = min(10.0, keyword_hits(text, KW_STRATEGY) * 2.0)

    # Sub-factor 2: Program management / architecture (+2 per hit, cap 10).
    s_pgm = min(10.0, keyword_hits(text, KW_PGM_ARCH) * 2.0)

    # Sub-factor 3: M&A integration — rarer signal, worth +3 per hit.
    s_ma = min(10.0, keyword_hits(text, KW_MA_INTEGRATION) * 3.0)

    # Sub-factor 4: Leadership scope inferred from title seniority.
    s_lead = _leadership_score(title)

    # Sub-factor 5: D2C / payments avoidance — start at 10, subtract 3 per hit.
    d2c_hits = keyword_hits(text, KW_D2C_PAYMENTS)
    s_no_d2c = max(0.0, 10.0 - d2c_hits * 3.0)

    # Sub-factor 6: Coding avoidance — start at 10, subtract 3 per hit.
    code_hits = keyword_hits(text, KW_HANDS_ON_CODE)
    s_no_code = max(0.0, 10.0 - code_hits * 3.0)

    return mean([s_strategy, s_pgm, s_ma, s_lead, s_no_d2c, s_no_code])


def _leadership_score(title: str) -> float:
    """Infer leadership scope from job title — returns 0-10.

    Important: "Principal" in an IC engineering/design/science context does
    NOT indicate leadership. "Principal Software Engineer" is still an IC role
    and must return 1.0, not 7.0. The function gate catches these before
    scoring, but this function is also used for _score_career_trajectory, so
    correctness matters regardless.
    """
    lo = title.lower

    # Detect IC "Principal [Engineer/Scientist/Designer/Developer/Researcher]"
    # the word "principal" here is a seniority band, not a leadership title.
    _IC_AFTER_PRINCIPAL = [
        "principal engineer", "principal scientist", "principal developer",
        "principal designer", "principal researcher", "principal data",
        "principal ml", "principal swe", "principal sde",
    ]
    if "principal" in lo and any(kw in lo for kw in _IC_AFTER_PRINCIPAL):
        return 1.0  # IC role despite "Principal" title band

    # C-suite and Group-level SVP/EVP → maximum.
    if any(t in lo for t in ["chief", "c-suite", "evp", "executive vice president", "group svp"]):
        return 10.0
    # SVP / VP / Head of → very high.
    if any(t in lo for t in ["svp", "senior vice president", "vice president", "vp ", " vp",
                               "head of", "general manager"]):
        return 9.0
    # Senior Director / Principal (strategy/leadership contexts).
    if any(t in lo for t in ["senior director", "principal", "managing director",
                               "practice lead", "distinguished"]):
        return 7.0
    # Director.
    if "director" in lo:
        return 6.0
    # Manager / Lead / Senior.
    if any(t in lo for t in ["senior manager", "lead ", " lead", "manager"]):
        return 4.0
    if "senior" in lo:
        return 3.0
    # Individual contributor.
    return 1.0


# ---------------------------------------------------------------------------
# Category 2: Industry alignment (weight 0.18)
# ---------------------------------------------------------------------------

def _score_industry(company_normalized: str, text: str) -> tuple[float, str]:
    """Map company + JD keywords to an industry bucket, return (score, bucket)."""
    bucket = detect_industry(company_normalized, text)
    return score_for_industry(bucket), bucket


# ---------------------------------------------------------------------------
# Category 3: Compensation (weight 0.15)
# ---------------------------------------------------------------------------

def _score_compensation(salary_min: Optional[int], salary_max: Optional[int],
                         description: str) -> float:
    """Piecewise score on base + bonus compensation.

    If salary is undisclosed: neutral (5.0).
    Benefits factor is inferred from keywords and averaged with comp score.
    Equity modifier is inferred from description keywords.
    """
    thresholds = COMP_THRESHOLDS or {}

    neutral      = float(thresholds.get("neutral_if_missing", 5.0))
    # Defaults are example placeholder values and only fire if scoring.yaml
    # is missing a comp_thresholds block. Live runs always get the
    # configured values from the YAML.
    high_min     = int(thresholds.get("high_min", 200_000))
    high_max     = int(thresholds.get("high_max", 260_000))
    overpay_max  = int(thresholds.get("overpay_max", 340_000))
    mid_upper    = int(thresholds.get("medium_upper", 200_000))
    mid_lower    = int(thresholds.get("medium_lower", 175_000))
    low_threshold = int(thresholds.get("low_threshold", 175_000))

    # Use salary_max as the reference (upside of the range matters most).
    # Cast to float because DynamoDB returns numeric attrs as decimal.Decimal,
    # which breaks the float-arithmetic interpolation below
    # (Decimal * float → TypeError). Source data is whole-dollar amounts so
    # float precision is fine.
    ref = salary_max if salary_max is not None else salary_min
    if ref is not None:
        ref = float(ref)
    if ref is None:
        comp_score = neutral
    elif ref < low_threshold:
        comp_score = 2.0
    elif ref < mid_lower:
        comp_score = 2.0
    elif ref < mid_upper:
        # Interpolate 5 → 8 across 180k-220k range.
        comp_score = 5.0 + ((ref - mid_lower) / (mid_upper - mid_lower)) * 3.0
    elif ref <= high_max:
        comp_score = 10.0
    elif ref <= overpay_max:
        comp_score = 9.0   # Slightly over-leveled risk
    else:
        comp_score = 7.0   # Significant overpay → title-mismatch risk

    # Benefits inference from keywords (0-10).
    # 4+ benefit terms → 10; 2-3 → 6; 1 → 3; 0 → 5 (neutral, don't penalize).
    benefit_kws = KW_FAMILY_FRIENDLY + [
        "healthcare", "dental", "vision", "401k", "401(k)", "pto",
        "paid time off", "equity", "bonus",
    ]
    benefit_hits = keyword_hits(description.lower, [b.lower for b in benefit_kws])
    if benefit_hits >= 4:
        benefits_score = 10.0
    elif benefit_hits >= 2:
        benefits_score = 6.0
    elif benefit_hits == 1:
        benefits_score = 3.0
    else:
        benefits_score = neutral  # Neutral — data not available

    # Equity modifier (added to the averaged comp+benefits score).
    desc_lo = description.lower
    equity_mod = 0.0
    if any(t in desc_lo for t in ["rsu", "rsus", "restricted stock"]):
        equity_mod = 1.0        # Public company liquid RSUs — most valuable
    elif any(t in desc_lo for t in ["series d", "series e", "series f"]):
        equity_mod = 0.8        # Late-stage, credible liquidity path
    elif any(t in desc_lo for t in ["series b", "series c"]):
        equity_mod = 0.5        # Mid-stage startup equity
    elif any(t in desc_lo for t in ["series a", "seed"]):
        equity_mod = 0.3        # Early-stage, speculative

    # Average comp_score and benefits_score, then add equity modifier.
    category_score = (comp_score + benefits_score) / 2.0 + equity_mod
    return min(10.0, category_score)


# ---------------------------------------------------------------------------
# Category 4: Geographic (weight 0.12) — score only; gate handled separately
# ---------------------------------------------------------------------------

# The geographic SCORE (0-10) is computed in gates.py alongside the gate
# multiplier. The engine receives it as a parameter from evaluate_all_gates.


# ---------------------------------------------------------------------------
# Category 5: Passion and identity alignment (weight 0.10)
# ---------------------------------------------------------------------------

def _score_passion(text: str, company_normalized: str, industry: str = "") -> float:
    """Sum of thematic bonus flags — each present adds 2 points, capped at 10.

    Flags:
      +2 "Helping people" mission signal
      +2 Gaming / interactive entertainment culture OR company is in a
         gaming-adjacent industry bucket (fix: Roblox/Epic JDs
         often don't say "f2p" or "live service" in so many words — they
         talk about "players", "creators", "avatars". Awarding credit
         based on the company's industry bucket ensures gaming studios
         aren't underscored relative to analyst firms that happen to use
         the word "strategy" a lot.)
      +2 Music industry connection
      +2 Immersive / experiential focus
      +2 Strategy / organizing systems
         (this only fires on pure gaming/immersive/music JDs
         now; analyst-firm JDs no longer double-dip here because they
         already get credit via industry_alignment.)
      +2 CCG / board-game connection
      +2 Accessibility / mental-health gaming (only when company is
         gaming-adjacent — tightening: unrelated HR boilerplate
         mentioning "accessibility" no longer fires)
    """
    # Industries that count as "gaming-adjacent" for the automatic
    # gaming-culture credit (regardless of JD wording).
    GAMING_ADJACENT = {
        "gaming_publisher_platform",
        "gaming_b2b_infrastructure",
        "digital_tcg_ccg",
        "immersive_lbe",
        "gaming_accessibility_nonprofit",
        "gaming_vc_pe_operating",
    }

    score = 0.0
    if any_match(text, KW_HELPING_PEOPLE):              score += 2.0

    # Gaming culture: fire if JD keywords match OR company is gaming-adjacent.
    if any_match(text, KW_GAMING_CULTURE) or industry in GAMING_ADJACENT:
        score += 2.0

    if any_match(text, KW_MUSIC):                       score += 2.0
    if any_match(text, KW_IMMERSIVE):                   score += 2.0

    # Strategy bucket: tightened in . Only award if the company is
    # in a passion-track industry (gaming, music, immersive, media). Before
    # this fix, Gartner/Forrester/NBCU JDs hit KW_STRATEGY heavily and got
    # a free +2 that was supposed to go to strategy-oriented gaming roles.
    STRATEGY_PASSION_INDUSTRIES = GAMING_ADJACENT | {
        "music_tech",
        "streaming_media",
        "sports_betting_tech",
    }
    if any_match(text, KW_STRATEGY) and industry in STRATEGY_PASSION_INDUSTRIES:
        score += 2.0

    if any_match(text, KW_CCG):                         score += 2.0

    # Accessibility / mental-health: tightened in . Only award if
    # company is gaming-adjacent. Every tech company's HR boilerplate
    # mentions "accessibility" — that generic signal was inflating unrelated
    # roles. Specific accessibility gaming companies still get it.
    accessibility_hit = any(kw in text for kw in [
        "accessible gaming", "ablegamers", "take this", "games for change",
        "suicide prevention",
    ])
    if accessibility_hit and industry in GAMING_ADJACENT:
        score += 2.0

    return min(10.0, score)


# ---------------------------------------------------------------------------
# Category 6: Work-life quality (weight 0.08)
# ---------------------------------------------------------------------------

def _score_work_life(text: str, company_normalized: str) -> float:
    """Start at 7.0 (neutral), adjust based on JD language and company reputation."""
    score = 7.0

    # Company-level crunch reputation penalty.
    co = company_normalized.lower
    if co in CRUNCH_REDUCED:
        score -= 1.0   # Reduced penalty — Riot Games has documented reform
    elif co in CRUNCH_COS:
        score -= 2.5   # Standard crunch penalty

    # JD keyword adjustments.
    score -= keyword_hits(text, KW_CRUNCH_PACE)    * 0.5
    score -= keyword_hits(text, KW_CULTURE_REDFLAGS) * 1.0
    score += keyword_hits(text, KW_FAMILY_FRIENDLY) * 0.5

    # Travel requirement penalty.
    travel_m = re.search(r"travel\s*(up to|:)?\s*(\d{2,3})\s*%", text, re.IGNORECASE)
    if travel_m:
        try:
            pct = int(travel_m.group(2))
            if pct >= 50:
                score -= 2.0
            elif pct >= 25:
                score -= 0.5
        except (ValueError, IndexError):
            pass

    # On-call / 24-7 penalty.
    if "on-call" in text or "24/7" in text or "24x7" in text:
        score -= 1.5

    return max(0.0, min(10.0, score))


# ---------------------------------------------------------------------------
# Category 7: Company health (weight 0.06)
# ---------------------------------------------------------------------------

def _score_company_health(company_normalized: str, description: str) -> float:
    """Base 7.0, adjusted by signals available at scrape time.

    External data (Crunchbase, layoffs.fyi, Glassdoor) is not fetched here;
    those enrichments are a future phase. We detect what we can from the JD.
    """
    score = 7.0
    desc_lo = description.lower

    # Positive: funding maturity signals.
    if any(t in desc_lo for t in ["nasdaq", "nyse", "publicly traded", "public company"]):
        score += 1.0
    if any(t in desc_lo for t in ["profitable", "profitability", "cash flow positive"]):
        score += 1.0
    if any(t in desc_lo for t in ["series d", "series e", "series f",
                                   "growth stage", "late stage"]):
        score += 2.0
    if any(t in desc_lo for t in ["series b", "series c"]):
        score += 1.0

    # Negative: explicit instability signals.
    if any(t in desc_lo for t in ["restructuring", "reorg", "cost cutting",
                                   "workforce reduction", "layoff"]):
        score -= 2.0
    if any(t in desc_lo for t in ["pre-revenue", "pre-product", "pre-launch"]):
        score -= 1.0

    return max(0.0, min(10.0, score))


# ---------------------------------------------------------------------------
# Category 8: Career trajectory (weight 0.05)
# ---------------------------------------------------------------------------

def _score_career_trajectory(title: str, description: str, company_normalized: str) -> float:
    """Title level + reporting line + scope + employer brand — averaged."""
    lo_title = title.lower
    lo_desc  = description.lower

    # Title level (0-10).
    s_title = _leadership_score(title)  # Reuse same function as role_fit.

    # Reporting line.
    reports_to_c = any(t in lo_desc for t in
                        ["reports to ceo", "reports to cto", "reports to coo",
                         "reports to cpo", "reports to ciso", "reports to president",
                         "directly to the ceo", "dotted line to ceo"])
    reports_to_dir = any(t in lo_desc for t in
                          ["reports to a director", "reports to the director",
                           "reports to director"])
    if reports_to_c:
        s_reporting = 10.0
    elif reports_to_dir:
        s_reporting = 4.0
    else:
        s_reporting = 6.0  # Neutral (reports to VP assumed)

    # Scope.
    s_scope = 6.0  # Neutral default.
    if any(t in lo_desc for t in ["global", "enterprise-wide", "company-wide",
                                   "cross-functional", "all business units"]):
        s_scope = 8.0
    if any(t in lo_desc for t in ["regional", "single market", "one country"]):
        s_scope = 5.0

    # Employer brand (Tier 1 target companies get a bump).
    is_tier1 = company_normalized in {
        "riot games", "roblox", "epic games", "netflix", "disney", "nbcuniversal",
        "warner bros discovery", "sony interactive entertainment", "microsoft",
        "gartner", "forrester", "draftkings", "fanduel", "betmgm",
    }
    s_brand = 8.0 if is_tier1 else 6.0

    return mean([s_title, s_reporting, s_scope, s_brand])


# ---------------------------------------------------------------------------
# Category 9: Cultural alignment (weight 0.03)
# ---------------------------------------------------------------------------

def _score_cultural(company_normalized: str, text: str) -> float:
    """HRC CEI 100 membership + JD LGBTQ+ signal detection.

    HRC 100 → 10.0
    Not rated but 3+ LGBTQ+ JD signals → 6.0
    No signals, not rated → 3.0
    JD mentions DEI rollback / "politically neutral" → 0.0
    """
    # DEI rollback is a hard disqualifier for cultural score.
    if any(t in text for t in ["politically neutral", "we don't do dei",
                                 "merit-based only", "end dei", "no pronouns"]):
        return 0.0

    co = company_normalized.lower.strip
    if co in HRC100:
        return 10.0

    lgbtq_hits = keyword_hits(text, KW_LGBTQ)
    if lgbtq_hits >= 3:
        return 6.0
    if lgbtq_hits >= 1:
        return 4.0
    return 3.0


# ---------------------------------------------------------------------------
# Category 10: Engagement type (weight 0.01)
# ---------------------------------------------------------------------------

def _score_engagement(text: str) -> float:
    """Full-time permanent = 9, interim = 10, fractional = 9, advisory = 7."""
    lo = text.lower

    if any(t in lo for t in ["interim", "fixed-term", "ftc", "short-term assignment",
                               "6-month", "12-month", "day rate"]):
        return 10.0
    if any(t in lo for t in ["fractional", "part-time retainer", "advisory role",
                               "board advisor"]):
        return 9.0
    if any(t in lo for t in ["advisory", "board seat"]):
        return 7.0
    if any(t in lo for t in ["3-month", "2-month", "short contract"]):
        return 5.0
    # Unpaid / equity-only — this ALSO triggers the engagement hard gate,
    # but in case it slips through scoring, return 0.
    if any(t in lo for t in ["unpaid", "equity only", "commission only"]):
        return 0.0
    # Default: full-time permanent assumed.
    return 9.0


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def score(job: dict, prefs: dict) -> dict:
    """Score a job dict against the original author's criteria.

    Args:
        job:   A job row dict (as stored in / returned from DynamoDB).
                Must have at minimum: title, company, company_normalized.
                Optional: description, location, remote, salary_min,
                          salary_max, company_tier.
        prefs: User preferences dict from UserPrefs table. Currently unused
               but wired in for personalisation hooks.

    Returns:
        {
          "score":             int 0-100,
          "tier":              str,
          "track":             str,
          "breakdown":         dict,
          "gates_triggered":   list,
          "modifiers_applied": list,
        }
    """
    title              = job.get("title", "") or ""
    description        = job.get("description", "") or ""
    company_normalized = job.get("company_normalized", "") or ""

    # Build a single concatenated text string for keyword searches.
    # Passed to most sub-functions to avoid repeated concatenation.
    text = build_text(job)

    # ------------------------------------------------------------------
    # Step 1: Detect industry and track (needed by gates and modifiers).
    # ------------------------------------------------------------------
    industry = detect_industry(company_normalized, text)
    track    = detect_track(text, industry)

    # ------------------------------------------------------------------
    # Step 2: Evaluate all gates.
    # ------------------------------------------------------------------
    # hard_gates: list of gate names that fired → final_score = 0
    # geo_gate:   0.0, 0.5, or 1.0 formula multiplier
    # eng_gate:   0.0 or 1.0 formula multiplier
    # geo_score:  0-10 geographic category score
    hard_gates, geo_gate, eng_gate, geo_score = evaluate_all_gates(job, text)

    # Any hard gate (seniority, compensation, geographic=0, engagement=0)
    # → short-circuit with score = 0.
    if hard_gates:
        return {
            "score":             0,
            "tier":              "skip",
            "track":             track,
            "breakdown":         {},
            "gates_triggered":   hard_gates,
            "modifiers_applied": ,
        }

    # ------------------------------------------------------------------
    # Step 3: Compute all ten category scores (0-10 each).
    # ------------------------------------------------------------------
    cat_scores = {
        "role_fit":           _score_role_fit(title, description),
        "industry_alignment": score_for_industry(industry),
        "compensation":       _score_compensation(
                                  job.get("salary_min"),
                                  job.get("salary_max"),
                                  description,
                              ),
        "geographic":         geo_score,
        "passion_identity":   _score_passion(text, company_normalized, industry),
        "work_life_quality":  _score_work_life(text, company_normalized),
        "company_health":     _score_company_health(company_normalized, description),
        "career_trajectory":  _score_career_trajectory(title, description, company_normalized),
        "cultural_alignment": _score_cultural(company_normalized, text),
        "engagement_type":    _score_engagement(text),
    }

    # ------------------------------------------------------------------
    # Step 4: Weighted sum → 0-10, then multiply by gates × 10.
    # ------------------------------------------------------------------
    category_sum = sum(
        cat_scores[cat] * WEIGHTS.get(cat, 0.0)
        for cat in cat_scores
    )  # 0-10 range

    # geo_gate and eng_gate are both 1.0 here (hard-gate cases already returned).
    # geo_gate may be 0.5 for ambiguous locations — that's the soft penalty path.
    raw_base = category_sum * geo_gate * eng_gate * 10.0   # 0-100

    # ------------------------------------------------------------------
    # Step 5: Modifier stack (additive delta).
    # ------------------------------------------------------------------
    modifier_delta, modifiers_applied = compute_modifiers(
        job, text, industry, geo_score, cat_scores
    )

    # ------------------------------------------------------------------
    # Step 6: Clamp, round, derive tier.
    # ------------------------------------------------------------------
    raw_final = raw_base + modifier_delta
    final     = max(0, min(100, round(raw_final)))
    tier      = tier_from_score(final)

    return {
        "score":             final,
        "tier":              tier,
        "track":             track,
        "breakdown":         {k: round(v, 2) for k, v in cat_scores.items},
        "gates_triggered":   ,
        "modifiers_applied": modifiers_applied,
    }
