"""Tests for src/scrapers/apify_linkedin.py — parse + salary parsing only.

No HTTP and no SSM. We exercise:
  - _parse_salary on a representative set of LinkedIn salary blob shapes
  - _extract_id_from_url for the URL-fallback path
  - parse across actor-key variations (jobUrl/url/applyUrl, etc.)

fetch involves Apify's REST API + SSM token retrieval and is verified
end-to-end via the live Phase-6 smoke test, not unit tests.
"""
from scrapers.apify_linkedin import (
    ApifyLinkedInScraper,
    _extract_id_from_url,
    _parse_salary,
)


# ---------- _parse_salary ---------------------------------------------------

def test_salary_dollars_with_commas:
    assert _parse_salary("$250,000 - $310,000") == (250_000, 310_000)


def test_salary_dollars_with_k_suffix:
    assert _parse_salary("$250K - $310K") == (250_000, 310_000)


def test_salary_no_dollar_sign:
    assert _parse_salary("USD 250000-310000") == (250_000, 310_000)


def test_salary_hourly_filtered_out:
    """We don't want $45/hour to score as a $45/year salary."""
    assert _parse_salary("$45/hour") == (None, None)
    assert _parse_salary("$45 per hour") == (None, None)
    assert _parse_salary("$45 hourly rate") == (None, None)


def test_salary_single_number_returns_none:
    """A single number isn't enough to be a min/max range."""
    assert _parse_salary("Up to $250,000") == (None, None)


def test_salary_empty_or_none:
    assert _parse_salary(None) == (None, None)
    assert _parse_salary("") == (None, None)


def test_salary_filters_trivially_small_numbers:
    """401K plan mentions etc shouldn't seed the parser."""
    # Only one candidate >= 20K, so should return None.
    assert _parse_salary("Has a 401k plan") == (None, None)


# ---------- _extract_id_from_url -------------------------------------------

def test_extract_id_from_view_url:
    assert _extract_id_from_url(
        "https://www.linkedin.com/jobs/view/3945102348/"
    ) == "3945102348"


def test_extract_id_from_url_with_query:
    assert _extract_id_from_url(
        "https://www.linkedin.com/jobs/view/3945102348/?refId=abc"
    ) == "3945102348"


def test_extract_id_no_match:
    assert _extract_id_from_url("https://example.com/foo") == ""
    assert _extract_id_from_url("") == ""


# ---------- parse ---------------------------------------------------------

def _payload(**over):
    """Realistic Apify item shape based on bebity actor output (per
    Section 4.2 of the phase plan). Override fields per test."""
    base = {
        "id":            "3945102348",
        "title":         "VP, Platform Engineering",
        "companyName":   "Acme Corp",
        "location":      "Los Angeles, CA (Remote)",
        "salary":        "$250,000 - $310,000",
        "description":   "Lead the platform engineering org.",
        "seniority":     "Executive",
        "postedTime":    "2026-04-14",
        "applyUrl":      "https://www.linkedin.com/jobs/view/3945102348/",
        "jobUrl":        "https://www.linkedin.com/jobs/view/3945102348/",
        "workplaceType": "remote",
        "_search_name":  "nyc-metro-vp-gaming",
    }
    base.update(over)
    return base


def test_parse_full_payload:
    s   = ApifyLinkedInScraper
    raw = s.parse(_payload)
    assert raw is not None
    # LinkedIn's numeric job id is globally unique — no slug prefix, otherwise
    # the same job returned by overlapping searches gets written 2-3 times.
    assert raw.native_id   == "3945102348"
    assert raw.title       == "VP, Platform Engineering"
    assert raw.company     == "Acme Corp"
    assert raw.url         == "https://www.linkedin.com/jobs/view/3945102348/"
    assert raw.location    == "Los Angeles, CA (Remote)"
    assert raw.description == "Lead the platform engineering org."
    assert raw.posted_at   == "2026-04-14"
    assert raw.salary_min  == 250_000
    assert raw.salary_max  == 310_000
    assert raw.remote is True


def test_parse_falls_back_to_url_for_id:
    """Some actor versions don't surface `id` separately — the numeric LI
    job id is only inside the URL. parse must dig it out."""
    s   = ApifyLinkedInScraper
    raw = s.parse(_payload(id=None, jobUrl="https://www.linkedin.com/jobs/view/9876543210/"))
    assert raw is not None
    assert raw.native_id == "9876543210"


def test_parse_uses_company_alias:
    """Older actors may emit `company` instead of `companyName`."""
    s   = ApifyLinkedInScraper
    raw = s.parse(_payload(companyName=None, company="Acme Corp"))
    assert raw.company == "Acme Corp"


def test_parse_remote_inferred_from_location:
    """When workplaceType is missing but location says Remote, infer True."""
    s   = ApifyLinkedInScraper
    raw = s.parse(_payload(workplaceType="", location="Remote - US"))
    assert raw.remote is True


def test_parse_onsite_workplace_type:
    s   = ApifyLinkedInScraper
    assert s.parse(_payload(workplaceType="on-site")).remote is False
    assert s.parse(_payload(workplaceType="onsite")).remote is False


def test_parse_hybrid_workplace_type_unknown:
    """Hybrid is neither remote nor on-site — leave as None."""
    s   = ApifyLinkedInScraper
    raw = s.parse(_payload(workplaceType="hybrid", location="New York, NY"))
    assert raw.remote is None


def test_parse_missing_title_skips:
    s = ApifyLinkedInScraper
    assert s.parse(_payload(title="")) is None


def test_parse_missing_id_and_no_url_skips:
    s = ApifyLinkedInScraper
    assert s.parse(_payload(id=None, jobUrl="", url="", applyUrl="")) is None


def test_parse_missing_salary_yields_none_min_max:
    s   = ApifyLinkedInScraper
    raw = s.parse(_payload(salary=None))
    assert raw.salary_min is None
    assert raw.salary_max is None


def test_parse_ignores_search_name_for_native_id:
    """`_search_name` is preserved on the raw payload (for the S3 archive)
    but must NOT prefix native_id — otherwise the same LinkedIn job returned
    by 2-3 overlapping searches gets written as 2-3 separate Jobs rows."""
    s = ApifyLinkedInScraper
    p = _payload
    p.pop("_search_name")
    raw = s.parse(p)
    assert raw.native_id == "3945102348"
