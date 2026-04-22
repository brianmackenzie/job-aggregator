"""Tests for src/scrapers/hnhiring.py — parse only."""
from scrapers.hnhiring import HNHiringScraper


def test_parse_canonical_pipe_format:
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "39123456",
        "parent_id": 39000000,
        "_story_id": 39000000,
        "text": "Acme | Senior Engineer | NY or Remote | $180k-$220k | Python, Go",
        "created_at": "2026-04-16T12:00:00Z",
    })
    assert raw is not None
    assert raw.native_id == "39123456"
    assert raw.company == "Acme"
    assert raw.title == "Senior Engineer"
    assert raw.remote is True
    assert raw.url == "https://news.ycombinator.com/item?id=39123456"


def test_parse_strips_html_entities:
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Acme &amp; Co | Engineer | NY<br>More info below.",
    })
    assert raw is not None
    assert raw.company == "Acme & Co"
    assert raw.title == "Engineer"


def test_parse_no_pipes_skips_if_implausible:
    """A freeform comment with no | splits shouldn't invent structure."""
    s = HNHiringScraper
    # Long single-line comments are likely narrative, not jobs.
    long_line = "A" * 200
    assert s.parse({"objectID": "1", "text": long_line}) is None


def test_parse_empty_text_skips:
    s = HNHiringScraper
    assert s.parse({"objectID": "1", "text": ""}) is None
    assert s.parse({"objectID": "1"}) is None


def test_parse_missing_id_skips:
    s = HNHiringScraper
    assert s.parse({"text": "Acme | Eng"}) is None


def test_parse_detects_remote_flag:
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Acme | Engineer | Onsite NY only",
    })
    assert raw is not None
    assert raw.remote is False


# ---------------------------------------------------------------------------
# Title-extraction regression tests — these patterns previously surfaced as
# bogus titles in the top-30 job list (URL / location / boilerplate strings
# that scored 50+ from company/industry modifiers).
# ---------------------------------------------------------------------------

def test_parse_skips_when_title_segment_is_url:
    """CrazyGames-shape: 'CrazyGames | https://about.crazygames.com/ | ...'"""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "CrazyGames | https://about.crazygames.com/ | Remote (Europe)",
    })
    # No segment looks like a role → skip.
    assert raw is None


def test_parse_skips_when_title_segment_is_location:
    """Cyngn-shape: 'Cyngn | Mountain View, CA | https://cyngn.com'"""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Cyngn | Mountain View, CA | https://cyngn.com",
    })
    assert raw is None


def test_parse_skips_when_title_segment_is_remote_tag:
    """Fold-shape: 'Fold | Remote (US Only) | ...'"""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Fold | Remote (US Only) | bitcoin rewards card",
    })
    assert raw is None


def test_parse_skips_when_title_is_boilerplate:
    """Russell Tobin-shape: 'Russell Tobin | RTA Posted Job Description | ...'"""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Russell Tobin | RTA Posted Job Description | NY",
    })
    assert raw is None


def test_parse_skips_tagline_with_no_role_keywords:
    """Neon Health-shape: 'Neon Health | AI in healthcare | Remote'"""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Neon Health | AI in healthcare | Remote",
    })
    # 'AI in healthcare' has no role keyword → skip.
    assert raw is None


def test_parse_picks_role_segment_past_url:
    """If parts[1] is a URL but parts[2] is a real role, use parts[2]."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Acme | https://acme.example | Senior Backend Engineer | Remote",
    })
    assert raw is not None
    assert raw.title == "Senior Backend Engineer"


def test_parse_picks_role_segment_past_location:
    """If parts[1] is a location but parts[2] is the role, use parts[2]."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Acme | Atlanta, GA | Director of Engineering | Onsite",
    })
    assert raw is not None
    assert raw.title == "Director of Engineering"


# ---------------------------------------------------------------------------
# semantic-review audit
# surfaced an HN row where the salary + URL string had landed in the
# `title` field and the real title ("Head of Engineering & Infrastructure")
# had landed in the `company` field.
#
# Two root causes: (1) URL path substrings like "engineer" were fooling
# _looks_like_title; (2) parser always assumed parts[0]=company.
# ---------------------------------------------------------------------------

def test_parse_rejects_url_anywhere_in_title_candidate:
    """A segment containing an inline URL must never be picked as the
    title, even if the URL path contains a role keyword substring."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": (
            "Acme | Mountain View, CA | "
            "$200k - $300k - https://acme.com/jobs/engineering_lead"
        ),
    })
    # parts[0]=Acme is company. parts[1] is a location. parts[2] contains
    # a URL (with "engineering_lead" as a path substring) — pre-fix, the
    # role-keyword sniff matched "engineer" inside the URL path. Post-fix,
    # the URL disqualifies the whole segment → no title → skip.
    assert raw is None


def test_parse_title_first_fetlife_regression:
    """The exact pattern from the semantic-review audit:
    title first, then a salary+URL blob, no clean company segment.
    Company must be recovered from the URL domain."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "47601882",
        "text": (
            "Head of Engineering &amp; Infrastructure | "
            "$182k - $272k USD - https://fetlife.com/jobs/"
            "head_of_engineering_and_infrastruct.."
        ),
    })
    assert raw is not None
    # Title-first format: parts[0] is the actual title.
    assert raw.title == "Head of Engineering & Infrastructure"
    # Company recovered from URL domain ("fetlife.com" → "Fetlife").
    # Substring-match-friendly; inner camel-case is not required.
    assert raw.company.lower == "fetlife"
    # Salary pulled out of the head text.
    assert raw.salary_min == 182000
    assert raw.salary_max == 272000


def test_parse_title_first_with_clean_company_segment:
    """Title-first format where parts[1] IS a plausible company — no
    URL needed. parts[0] → title, parts[1] → company."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Director of Platform Engineering | Acme | Remote US | $220k-$260k",
    })
    assert raw is not None
    assert raw.title == "Director of Platform Engineering"
    assert raw.company == "Acme"
    assert raw.salary_min == 220000
    assert raw.salary_max == 260000


def test_parse_salary_extraction_in_canonical_format:
    """Salary columns should now be populated for the canonical format."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Acme | Senior Engineer | NY or Remote | $180k-$220k | Python, Go",
    })
    assert raw is not None
    assert raw.salary_min == 180000
    assert raw.salary_max == 220000


def test_parse_salary_absent_stays_none:
    """No salary in the head → salary_min / salary_max stay None (not 0)."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Acme | Senior Engineer | NY or Remote",
    })
    assert raw is not None
    assert raw.salary_min is None
    assert raw.salary_max is None


def test_parse_does_not_swap_when_parts0_is_a_real_company:
    """Regression guard: a company name with a mildly-titley substring
    (e.g. 'ProductHunt' contains 'product' which is in the GENERAL
    role-keyword list but NOT the STRONG list) must not be treated as
    a title. The canonical format still wins."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "ProductHunt | Senior Engineer | Remote | $180k-$220k",
    })
    assert raw is not None
    assert raw.company == "ProductHunt"
    assert raw.title == "Senior Engineer"


def test_parse_does_not_swap_cvector_regression:
    """Regression: 'CVector' at parts[0] contains 'cto' as letters 3-5
    but is NOT the acronym — it's a company name. The old substring-
    match version of _looks_strongly_like_title falsely flagged it as a
    title candidate and swapped, producing title='CVector' and
    company='New York City (FiDi)'. The new word-boundary pattern must
    leave this row alone."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "47609341",
        "text": (
            "CVector | Software Engineers, Senior Research Engineer | "
            "New York City (FiDi) | ONSITE | Full-time | "
            "VISA SPONSORSHIP CVector builds software."
        ),
    })
    assert raw is not None
    # Canonical layout preserved: company first, title second.
    assert raw.company == "CVector"
    assert raw.title == "Software Engineers, Senior Research Engineer"


def test_parse_still_handles_cto_title_correctly:
    """Counter-regression: 'CTO of Platform | Acme | Remote' must still
    trigger the title-first swap. Word boundaries let real 'CTO' through
    while rejecting embedded 'cto' in 'CVector'."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "CTO of Platform | Acme | Remote | $250k",
    })
    assert raw is not None
    assert raw.title == "CTO of Platform"
    assert raw.company == "Acme"


def test_parse_url_company_strips_careers_subdomain:
    """`careers.example.com` and the like should yield 'Example',
    not 'Careers'."""
    from scrapers.hnhiring import _company_from_url
    assert _company_from_url("see https://careers.stripe.com/jobs/foo") == "Stripe"
    assert _company_from_url("https://www.acme.io/about") == "Acme"
    # No URL in text → None (caller falls back to different logic).
    assert _company_from_url("no url here at all") is None
    # Greenhouse/Lever/etc. return None (see the ATS-host test below).
    # That used to assert 'Greenhouse' as the company, which was wrong —
    # Greenhouse is the ATS, not the employer.


# ---------------------------------------------------------------------------
# blocklist regression tests. The first backfill dry-run
# surfaced rows where title-first parsing found a plausible-looking
# company in parts[1:] but the "company" was actually a city or an
# employment-type tag. Fix: curated blocklist in _NOT_A_COMPANY_EXACT
# AND prefer URL-domain over segment scan when a URL is present.
# ---------------------------------------------------------------------------

def test_looks_like_company_rejects_city_names:
    """Bare city names must never be treated as companies."""
    from scrapers.hnhiring import _looks_like_company
    assert _looks_like_company("Chicago") is False
    assert _looks_like_company("NYC") is False
    assert _looks_like_company("San Francisco") is False
    assert _looks_like_company("London") is False
    # But a company name that *contains* a city substring should still pass.
    assert _looks_like_company("Chicago Trading Partners") is True


def test_looks_like_company_rejects_employment_types:
    """'Full-Time', 'Contract', 'Visa Sponsorship' etc. aren't companies."""
    from scrapers.hnhiring import _looks_like_company
    assert _looks_like_company("Full-Time") is False
    assert _looks_like_company("full time") is False
    assert _looks_like_company("Contract") is False
    assert _looks_like_company("Visa Sponsorship") is False
    assert _looks_like_company("W2") is False


def test_parse_title_first_prefers_url_over_bad_segment:
    """Title-first post with a URL and an employment-type segment:
    the URL must win. Pre-fix, parts[1]='Full-Time' was accepted as
    company."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": (
            "Head of Engineering | Full-Time | "
            "https://fetlife.com/jobs/head_of_eng"
        ),
    })
    assert raw is not None
    assert raw.title == "Head of Engineering"
    # URL domain wins over the "Full-Time" segment.
    assert raw.company.lower == "fetlife"


def test_parse_title_first_skips_when_only_city_and_no_url:
    """Title-first post with a city in parts[1] and NO url: we can't
    recover a real company, so skip rather than invent 'Chicago' as
    the employer."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": "Senior Engineer | Chicago | Onsite",
    })
    # No URL + no plausible company segment → skip.
    assert raw is None


def test_company_from_url_rejects_ats_hosts:
    """A URL pointing to an ATS host (Greenhouse, Lever, etc.) must
    NOT be used as the company name — those are hosting platforms,
    not employers."""
    from scrapers.hnhiring import _company_from_url
    # Greenhouse — including the job-boards.greenhouse.io pattern.
    assert _company_from_url("https://boards.greenhouse.io/acme/jobs/1") is None
    assert _company_from_url("https://job-boards.greenhouse.io/acme") is None
    assert _company_from_url("https://greenhouse.io/foo") is None
    # Lever / Ashby / Workable / BambooHR.
    assert _company_from_url("https://jobs.lever.co/acme/123") is None
    assert _company_from_url("https://jobs.ashbyhq.com/acme") is None
    assert _company_from_url("https://apply.workable.com/acme") is None
    # Real company URL still works.
    assert _company_from_url("https://fetlife.com/jobs/x") == "Fetlife"


def test_parse_skips_title_first_when_url_is_only_ats:
    """Title-first post whose only URL points to an ATS AND has no
    plausible company segment: must skip. We shouldn't pick the ATS
    as the employer."""
    s = HNHiringScraper
    raw = s.parse({
        "objectID": "1",
        "text": (
            "Senior Engineer | Remote | "
            "https://boards.greenhouse.io/unknown/jobs/99"
        ),
    })
    assert raw is None
