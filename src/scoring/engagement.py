"""engagement — categorical detection of role engagement type.

the algo's `engagement_type` weight was zeroed out
(the original author's call: "engagement type should probably be removed and just noted
in the listing"). This module replaces the score contribution with a
categorical label that the frontend surfaces as a filter chip + card chip,
the same way `work_mode` already does for remote/hybrid/onsite.

Public API:
    detect_engagement(job: dict) -> str
        Returns one of: "fulltime" | "contract" | "interim_fractional"
                       | "advisor" | "unclear"

Why categorical, not scored?
    * the original author doesn't WANT the algo to push contract roles up or down — he
      wants to FILTER on it. Some weeks he's open to contract; some weeks
      he wants permanent only. A score weight can't express that.
    * "Engagement" is an orthogonal dimension to fit / comp / geo. A
      perfect-fit interim CTO at a target company shouldn't be deflated
      just because the engagement is short-term.
    * The detection is cheap (regex over title + description); reusing
      the existing keyword lists keeps it tunable from scoring.yaml.

Detection priority (first match wins, more specific → less specific):
    1. interim_fractional   — "Interim CTO", "Fractional CIO", "Fractional CMO"
                              These are the original author's TRACK_2 explicit targets.
    2. advisor              — "Advisor", "Operating Partner", "Strategic
                              Advisor", "Executive Advisor", "EIR".
                              TRACK_3 territory.
    3. contract             — "Contract", "1099", "W2 Contract", "Day Rate",
                              "(Temporary)", "Short-term", "Project-based".
                              Catches staff-aug roles + fixed-term gigs.
    4. fulltime             — Default when none of the above match AND the
                              title looks like a real perm role. Anything
                              not classifiable as the above three buckets
                              and not obviously broken falls here.
    5. unclear              — Reserved for cases where title + description
                              are too sparse to tell. In practice we rarely
                              return this — most JDs have enough text.
"""
from __future__ import annotations

# These keyword lists deliberately stay tighter than the scoring keyword
# lists (config/scoring.yaml::keywords.interim_fractional). The scoring
# list is wider because it includes single words like "engagement" and
# "advisor" that fire too aggressively for a categorical chip — e.g. a
# JD that says "advise the CFO" should not flip a perm VP role to
# "advisor" engagement. We keep the chip-grade list literal-prefix-y.

# Order = priority. Each entry: (label, list of phrases to match in
# lowercased title|description). First label whose phrase hits wins.
_ENGAGEMENT_RULES: list[tuple[str, list[str]]] = [
    (
        "interim_fractional",
        [
            "interim cto", "interim cio", "interim cpo", "interim coo",
            "interim chief", "interim vp", "interim head of",
            "fractional cto", "fractional cio", "fractional cpo",
            "fractional coo", "fractional cfo", "fractional cmo",
            "fractional chief", "fractional vp", "fractional head of",
            "fractional executive",
            # Title-form fractional/interim that doesn't carry a scope word
            # (rare but happens — "Interim Leadership", "Fractional Tech Leader")
            "interim leadership", "fractional leadership",
        ],
    ),
    (
        "advisor",
        [
            "advisor", "advisory role", "advisory board",
            "operating partner", "executive advisor",
            "strategic advisor", "board advisor", "technical advisor",
            "executive in residence", " eir ", "(eir)",
        ],
    ),
    (
        "contract",
        [
            # Explicit contract-shape titles + JD phrases.
            "(contract)", "(contractor)",
            "contract-to-hire", "contract to hire",
            " 1099 ", " 1099,", "(1099)",
            "w2 contract", "w-2 contract",
            "day rate", "hourly rate",
            "(temporary)", " temporary ", "temp-to-perm", "temp to perm",
            "short-term assignment", "short term assignment",
            "(short term)", "(short-term)",
            "project-based", "project based",
            "fixed-term", "fixed term contract", " ftc,",
            "6-month contract", "12-month contract",
            "statement of work", " sow ", "(sow)",
            "consulting engagement",
        ],
    ),
]


def detect_engagement(job: dict) -> str:
    """Return one of: interim_fractional | advisor | contract | fulltime | unclear.

    See module docstring for the detection priority. Reads job['title'] and
    job['description']; tolerates either being missing.
    """
    title = (job.get("title") or "").lower
    desc  = (job.get("description") or "").lower
    # Match against title FIRST — title-shaped engagement signals are far
    # more reliable than description mentions. A JD that says "we hired
    # consultants in the past" should not flip a perm role to "contract".
    title_padded = f" {title} "
    desc_padded  = f" {desc} "

    for label, phrases in _ENGAGEMENT_RULES:
        for ph in phrases:
            if ph in title_padded:
                return label

    # No title match. Fall back to description scan — same priority order,
    # but stricter: the description has to mention the phrase pretty
    # explicitly to count.
    for label, phrases in _ENGAGEMENT_RULES:
        for ph in phrases:
            if ph in desc_padded:
                return label

    # Nothing engagement-y said anywhere. Default = full-time perm.
    # (We rarely return "unclear" — most JDs have enough title text to
    # tell. Reserve "unclear" for jobs where title is empty / placeholder,
    # which would already be a data-quality issue elsewhere.)
    if not title.strip:
        return "unclear"
    return "fulltime"


# ---------------------------------------------------------------------
# Display labels — used by the API /api/taxonomy response so the chip
# rail can render readable text. Keep keys aligned with detect_engagement
# return values.
# ---------------------------------------------------------------------
ENGAGEMENT_LABELS: dict[str, str] = {
    "fulltime":            "Full-time",
    "contract":            "Contract",
    "interim_fractional":  "Interim / Fractional",
    "advisor":             "Advisor",
    "unclear":             "Unclear",
}
