"""Tests for src/scrapers/smartrecruiters.py — parse + normalize only.

Payload shapes verified against
    https://api.smartrecruiters.com/v1/companies/AcmeMedia3/postings/{id}
on 2026-04-18. SR's public v1 API is stable and contract-versioned, so
these shapes are unlikely to drift. Network-touching helpers (_fetch_company,
_fetch_detail) are exercised end-to-end by manual smoke tests, not unit tests
— here we focus on the pure transformation layer.
"""
from scrapers.smartrecruiters import (
    SmartRecruitersScraper,
    _join_jobad_sections,
    _strip_html,
)


# ---------------------------------------------------------------------------
# _strip_html / _join_jobad_sections — pure helpers
# ---------------------------------------------------------------------------

def test_strip_html_basic:
    """Tags removed, whitespace collapsed, common entities decoded."""
    s = "<p>Hello&nbsp;<b>world</b></p>\n  <span>!</span>"
    assert _strip_html(s) == "Hello world !"


def test_strip_html_handles_none:
    """Defensive: many SR sections are absent — must not crash."""
    assert _strip_html("") == ""
    assert _strip_html(None) == ""   # type: ignore[arg-type]


def test_join_jobad_sections_orders_deterministically:
    """Order is fixed (companyDesc, jobDesc, qualifications, additional) so
    the stored description is diff-stable across re-fetches."""
    jobad = {
        "sections": {
            "additionalInformation": {"text": "<p>Z</p>"},
            "companyDescription":    {"text": "<p>A</p>"},
            "qualifications":        {"text": "<p>Y</p>"},
            "jobDescription":        {"text": "<p>B</p>"},
        }
    }
    # A then B then Y then Z — never insertion order from the dict.
    assert _join_jobad_sections(jobad) == "A B Y Z"


def test_join_jobad_sections_skips_missing:
    """Absent sections are silently skipped — no extra whitespace."""
    jobad = {"sections": {"jobDescription": {"text": "<p>only this</p>"}}}
    assert _join_jobad_sections(jobad) == "only this"


def test_join_jobad_sections_empty_safe:
    """Defensive: empty dict / None / no `sections` key all return ''."""
    assert _join_jobad_sections({}) == ""
    assert _join_jobad_sections(None) == ""   # type: ignore[arg-type]
    assert _join_jobad_sections({"sections": {}}) == ""


# ---------------------------------------------------------------------------
# parse — DETAIL payload → RawJob
# ---------------------------------------------------------------------------

def _payload(**over):
    """Minimal valid SmartRecruiters DETAIL payload. Override fields per test.

    Mirrors the live shape returned by
        GET /v1/companies/AcmeMedia3/postings/744000121583177
    """
    base = {
        "id":         "744000121583177",
        "name":       "Director, Analytics & AI Solutions",
        "uuid":       "5b36caa5-f2c9-41b2-826e-572f0e9ad350",
        "refNumber":  "51622501",
        "company":    {"identifier": "AcmeMedia3", "name": "AcmeMedia"},
        "releasedDate": "2026-04-18T15:20:45.005Z",
        "location": {
            "city":         "Burbank",
            "region":       "CA",
            "country":      "us",
            "fullLocation": "Burbank, CA, United States",
            "remote":       False,
            "hybrid":       True,
        },
        "postingUrl": "https://jobs.smartrecruiters.com/AcmeMedia3/744000121583177-director-analytics-ai-solutions",
        "applyUrl":   "https://jobs.smartrecruiters.com/AcmeMedia3/744000121583177-director-analytics-ai-solutions?oga=true",
        "jobAd": {
            "sections": {
                "companyDescription": {"text": "<p>About AcmeMedia.</p>"},
                "jobDescription":     {"text": "<p>Build AI solutions.</p>"},
                "qualifications":     {"text": "<p>10y experience.</p>"},
            }
        },
        "_company_meta": {
            "name":     "AcmeMedia",
            "ats_slug": "AcmeMedia3",
            "tier":     "1",
            "industry": "streaming_media",
        },
    }
    base.update(over)
    return base


def test_parse_full_payload:
    s   = SmartRecruitersScraper
    raw = s.parse(_payload)
    assert raw is not None
    # native_id is prefixed with the SR company id so two tenants can't
    # ever collide on numeric posting IDs.
    assert raw.native_id == "AcmeMedia3:744000121583177"
    assert raw.title     == "Director, Analytics & AI Solutions"
    assert raw.company   == "AcmeMedia"
    # postingUrl is preferred over applyUrl (cleaner share link).
    assert raw.url       == "https://jobs.smartrecruiters.com/AcmeMedia3/744000121583177-director-analytics-ai-solutions"
    assert raw.location  == "Burbank, CA, United States"
    # All 3 sections concatenated, HTML stripped, in deterministic order.
    assert raw.description == "About AcmeMedia. Build AI solutions. 10y experience."
    assert raw.posted_at   == "2026-04-18T15:20:45.005Z"
    # Explicit `remote: false` from SR is honoured (not None).
    assert raw.remote is False
    assert raw.raw["company_tier"] == "1"


def test_parse_remote_true_passes_through:
    """SR's explicit remote boolean is preserved verbatim."""
    s   = SmartRecruitersScraper
    raw = s.parse(_payload(location={"remote": True, "fullLocation": "Remote, US"}))
    assert raw.remote is True


def test_parse_no_remote_field_yields_none:
    """When SR omits the `remote` key, we leave the flag unset so the
    scoring engine's text-based heuristics decide."""
    s   = SmartRecruitersScraper
    raw = s.parse(_payload(location={"fullLocation": "London, UK"}))
    assert raw.remote is None


def test_parse_falls_back_to_apply_url:
    """If postingUrl is somehow absent we use applyUrl."""
    s   = SmartRecruitersScraper
    raw = s.parse(_payload(postingUrl=None))
    assert raw.url == "https://jobs.smartrecruiters.com/AcmeMedia3/744000121583177-director-analytics-ai-solutions?oga=true"


def test_parse_builds_location_from_parts_when_full_missing:
    """If `fullLocation` is absent we synthesize 'city, region, country'."""
    s   = SmartRecruitersScraper
    raw = s.parse(_payload(location={"city": "London", "country": "uk"}))
    assert raw.location == "London, uk"


def test_parse_skips_when_no_id:
    """Malformed payload (no id) → return None, never crash."""
    s = SmartRecruitersScraper
    assert s.parse(_payload(id=None)) is None


def test_parse_skips_when_no_title:
    s = SmartRecruitersScraper
    assert s.parse(_payload(name="")) is None
    assert s.parse(_payload(name="   ")) is None


def test_parse_skips_when_no_company:
    """company_normalized drives the CompanyIndex GSI — empty would fail
    the DynamoDB write. We skip cleanly instead."""
    s = SmartRecruitersScraper
    p = _payload(company={"identifier": "x"}, _company_meta={"ats_slug": "x"})
    assert s.parse(p) is None


def test_parse_falls_back_to_company_block_name:
    """If _company_meta has no name, use the company block's name field."""
    s = SmartRecruitersScraper
    p = _payload(_company_meta={"ats_slug": "AcmeMedia3"})
    raw = s.parse(p)
    assert raw is not None
    assert raw.company == "AcmeMedia"


def test_parse_handles_missing_jobad_gracefully:
    """jobAd.sections may be entirely absent on draft postings — must
    not crash; description should just be None."""
    s   = SmartRecruitersScraper
    raw = s.parse(_payload(jobAd={}))
    assert raw is not None
    assert raw.description is None


def test_normalize_injects_company_tier:
    """The scoring engine reads company_tier off the row to apply the
    tier modifier — verify it lands on the normalised dict."""
    s   = SmartRecruitersScraper
    raw = s.parse(_payload)
    row = s.normalize(raw)
    assert row["company_tier"] == "1"


def test_normalize_omits_company_tier_when_absent:
    """If a company has no tier, the field should be omitted (not '')."""
    s   = SmartRecruitersScraper
    raw = s.parse(_payload(_company_meta={"name": "Acme", "ats_slug": "acme"}))
    row = s.normalize(raw)
    assert "company_tier" not in row
