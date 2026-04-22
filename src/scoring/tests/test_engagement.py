"""engagement detection tests.

Engagement type was removed as a scored signal and replaced with a
categorical label that the frontend filters on. These tests pin down
the detection priority (interim_fractional > advisor > contract >
fulltime) and the title-vs-description bias (title-shaped signals
should win).

Run locally (from repo root):
    python -m pytest src/scoring/tests/test_engagement.py -v
"""
from scoring.engagement import detect_engagement, ENGAGEMENT_LABELS


# ---------------------------------------------------------------------------
# interim / fractional — the original author's TRACK_2 explicit targets
# ---------------------------------------------------------------------------

class TestInterimFractional:
    def test_interim_cto_title(self):
        assert detect_engagement({
            "title": "Interim CTO",
            "description": "Lead the engineering org for 6 months.",
        }) == "interim_fractional"

    def test_fractional_cmo_title(self):
        assert detect_engagement({
            "title": "Fractional CMO",
            "description": "",
        }) == "interim_fractional"

    def test_interim_head_of_product(self):
        assert detect_engagement({
            "title": "Interim Head of Product",
            "description": "",
        }) == "interim_fractional"

    def test_fractional_executive_title(self):
        assert detect_engagement({
            "title": "Fractional Executive — Growth",
            "description": "",
        }) == "interim_fractional"

    def test_interim_in_description_only(self):
        # Title is generic; description discloses interim engagement.
        assert detect_engagement({
            "title": "Head of Product",
            "description": "This is an interim CTO-style engagement, ~6 months.",
        }) == "interim_fractional"


# ---------------------------------------------------------------------------
# advisor — TRACK_3 territory
# ---------------------------------------------------------------------------

class TestAdvisor:
    def test_operating_partner_title(self):
        assert detect_engagement({
            "title": "Operating Partner",
            "description": "PE-backed portfolio operator.",
        }) == "advisor"

    def test_strategic_advisor_title(self):
        assert detect_engagement({
            "title": "Strategic Advisor, Web3 Gaming",
            "description": "",
        }) == "advisor"

    def test_eir_with_padding(self):
        # ' eir ' must match — guarded by spaces so it doesn't fire on
        # words like "their" or "weird".
        assert detect_engagement({
            "title": "EIR — Consumer AI",
            "description": "Executive in residence on a 12-month track.",
        }) == "advisor"

    def test_advise_the_cfo_does_not_flip_perm_role(self):
        # Bug guard: the verb "advise" appearing in a JD should NOT
        # downgrade a perm VP role to "advisor". The chip-grade keyword
        # list is literal-prefix-y for exactly this reason.
        assert detect_engagement({
            "title": "VP of Finance",
            "description": "Partner with the CEO and advise the CFO on capital strategy.",
        }) == "fulltime"


# ---------------------------------------------------------------------------
# contract — staff-aug + fixed-term gigs
# ---------------------------------------------------------------------------

class TestContract:
    def test_parenthetical_contract_in_title(self):
        assert detect_engagement({
            "title": "Senior Product Manager (Contract)",
            "description": "",
        }) == "contract"

    def test_contract_to_hire_title(self):
        assert detect_engagement({
            "title": "Director of Engineering (contract-to-hire)",
            "description": "",
        }) == "contract"

    def test_day_rate_in_description(self):
        assert detect_engagement({
            "title": "Principal Designer",
            "description": "12-month contract, day rate negotiable.",
        }) == "contract"

    def test_six_month_contract_in_description(self):
        assert detect_engagement({
            "title": "Senior Engineer",
            "description": "This is a 6-month contract with possible extension.",
        }) == "contract"

    def test_consultants_history_does_not_flip_perm(self):
        # Guard: "we hired consultants in the past" should not flip a
        # perm role to contract.
        assert detect_engagement({
            "title": "Director of Product",
            "description": "We have hired consultants in the past for this work.",
        }) == "fulltime"


# ---------------------------------------------------------------------------
# fulltime — the default
# ---------------------------------------------------------------------------

class TestFulltime:
    def test_plain_vp_role_is_fulltime(self):
        assert detect_engagement({
            "title": "VP of Product, Player Experience",
            "description": "Own the product roadmap. Full-time, hybrid NYC.",
        }) == "fulltime"

    def test_director_with_no_engagement_signal(self):
        assert detect_engagement({
            "title": "Director of Engineering",
            "description": "",
        }) == "fulltime"


# ---------------------------------------------------------------------------
# unclear — only fires when title is empty / placeholder
# ---------------------------------------------------------------------------

class TestUnclear:
    def test_empty_title_returns_unclear(self):
        assert detect_engagement({
            "title": "",
            "description": "",
        }) == "unclear"

    def test_whitespace_title_returns_unclear(self):
        assert detect_engagement({
            "title": "   ",
            "description": "",
        }) == "unclear"

    def test_missing_title_returns_unclear(self):
        # No 'title' key at all — tolerated, returns unclear.
        assert detect_engagement({
            "description": "Lead the team.",
        }) == "unclear"


# ---------------------------------------------------------------------------
# priority — first-rule-wins when multiple signals collide
# ---------------------------------------------------------------------------

class TestPriority:
    def test_interim_beats_advisor(self):
        # Title says interim; JD also says advisor — interim wins.
        assert detect_engagement({
            "title": "Interim CTO",
            "description": "You'll act as a strategic advisor to the board.",
        }) == "interim_fractional"

    def test_advisor_beats_contract(self):
        # Title says advisor; JD mentions a 6-month contract — advisor wins.
        assert detect_engagement({
            "title": "Operating Partner",
            "description": "12-month contract with the firm.",
        }) == "advisor"

    def test_title_beats_description(self):
        # Title is plain perm; description mentions contract — but title
        # is scanned FIRST and finds nothing engagement-y, so the
        # description scan kicks in and contract wins. Documents the
        # bias: title-first, then description.
        assert detect_engagement({
            "title": "Director of Product",
            "description": "This is offered as a 6-month contract.",
        }) == "contract"


# ---------------------------------------------------------------------------
# labels — taxonomy round-trip
# ---------------------------------------------------------------------------

class TestLabels:
    def test_every_detection_value_has_a_label(self):
        # The /api/taxonomy endpoint reads ENGAGEMENT_LABELS to populate
        # the chip rail; every value detect_engagement can return must
        # have a human-readable label.
        for value in ("fulltime", "contract", "interim_fractional",
                      "advisor", "unclear"):
            assert value in ENGAGEMENT_LABELS
            assert ENGAGEMENT_LABELS[value]   # non-empty
