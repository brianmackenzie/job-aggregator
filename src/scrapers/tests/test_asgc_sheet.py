"""Tests for src/scrapers/asgc_sheet.py — parse + header alias logic.

No HTTP. fetch is exercised by the live smoke test once the maintainer's
csv_url is wired into config/sources.yaml.
"""
from scrapers.asgc_sheet import ASGCSheetScraper, _pick


# ---------- _pick (header aliasing) ----------------------------------------

def test_pick_first_match_wins:
    row = {"Company": "Acme", "Employer": "ShouldBeIgnored"}
    assert _pick(row, ("company", "employer")) == "Acme"


def test_pick_falls_back_to_alias:
    """When the maintainer used 'Employer' instead of 'Company'."""
    row = {"Employer": "Acme"}
    assert _pick(row, ("company", "employer")) == "Acme"


def test_pick_case_and_whitespace_insensitive:
    row = {"  COMPANY  ": "Acme"}
    assert _pick(row, ("company",)) == "Acme"


def test_pick_skips_empty_values:
    """Empty cells should not be treated as matches."""
    row = {"Company": "", "Employer": "Acme"}
    assert _pick(row, ("company", "employer")) == "Acme"


def test_pick_returns_empty_when_no_match:
    row = {"Foo": "Bar"}
    assert _pick(row, ("company", "employer")) == ""


# ---------- parse --------------------------------------------------------

def _row(**over):
    base = {
        "Company":  "Acme Corp",
        "Role":     "VP, Platform Engineering",
        "Location": "Los Angeles, CA",
        "Posted":   "2026-04-12",
        "URL":      "https://www.example.com/en/work-with-us/job/12345",
        "Notes":    "Lead the platform org.",
        "_row_index": 7,
    }
    base.update(over)
    return base


def test_parse_canonical_row:
    s   = ASGCSheetScraper
    raw = s.parse(_row)
    assert raw is not None
    # native_id is the URL slug.
    assert raw.native_id  == "12345"
    assert raw.company    == "Acme Corp"
    assert raw.title      == "VP, Platform Engineering"
    assert raw.location   == "Los Angeles, CA"
    assert raw.posted_at  == "2026-04-12"
    assert raw.url        == "https://www.example.com/en/work-with-us/job/12345"
    assert raw.description == "Lead the platform org."
    # remote is unknown from a freeform sheet.
    assert raw.remote is None


def test_parse_alternate_header_spellings:
    """Maintainers freely rename columns. We accept the alias set."""
    s   = ASGCSheetScraper
    raw = s.parse({
        "Employer":     "Example Studio",
        "Title":        "Director, Game Engineering",
        "Region":       "Bellevue, WA",
        "Date":         "2026-04-10",
        "Apply URL":    "https://example.com/jobs/abc",
        "Description":  "Lead the engine team.",
        "_row_index":   2,
    })
    assert raw is not None
    assert raw.company  == "Example Studio"
    assert raw.title    == "Director, Game Engineering"
    assert raw.location == "Bellevue, WA"
    assert raw.posted_at == "2026-04-10"
    assert raw.url      == "https://example.com/jobs/abc"
    assert raw.description == "Lead the engine team."


def test_parse_skips_rows_missing_company:
    s = ASGCSheetScraper
    assert s.parse(_row(Company="")) is None


def test_parse_skips_rows_missing_role:
    s = ASGCSheetScraper
    assert s.parse(_row(Role="")) is None


def test_parse_uses_row_index_when_url_missing:
    """No URL column / blank URL → fall back to a stable row-index id so
    we don't drop the row entirely."""
    s   = ASGCSheetScraper
    raw = s.parse(_row(URL=""))
    assert raw is not None
    assert raw.native_id == "row-7"
    # url is empty string when no URL column present.
    assert raw.url == ""


def test_parse_url_with_trailing_slash:
    """URL slug extraction shouldn't be confused by a trailing slash."""
    s   = ASGCSheetScraper
    raw = s.parse(_row(URL="https://example.com/jobs/abc-123/"))
    assert raw.native_id == "abc-123"


def test_parse_skip_when_unrecognized_schema:
    """Sheet with completely unrelated columns → skip."""
    s = ASGCSheetScraper
    assert s.parse({"Foo": "Bar", "Baz": "Quux"}) is None
