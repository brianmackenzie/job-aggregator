"""Tests for src/scrapers/weworkremotely.py — parse + helpers only.

No HTTP. We exercise:
  - _split_company_and_title on the "<Company>: <Role>" convention
  - _strip_html on RSS HTML descriptions
  - parse across realistic and malformed RSS items

fetch is HTTP-bound and exercised by the live smoke test, not unit tests.
"""
from scrapers.weworkremotely import (
    WeWorkRemotelyScraper,
    _split_company_and_title,
    _strip_html,
)


# ---------- _split_company_and_title ----------------------------------------

def test_split_basic:
    assert _split_company_and_title("Acme Corp: Senior Engineer") == (
        "Acme Corp", "Senior Engineer",
    )


def test_split_strips_whitespace:
    assert _split_company_and_title("  Acme  :  Engineer  ") == (
        "Acme", "Engineer",
    )


def test_split_uses_leftmost_colon:
    """Roles can contain colons — split on the FIRST one."""
    assert _split_company_and_title("Acme: Senior Engineer: Backend") == (
        "Acme", "Senior Engineer: Backend",
    )


def test_split_no_colon_returns_empty_company:
    """Caller treats empty company as 'skip'."""
    assert _split_company_and_title("Just a freeform title") == (
        "", "Just a freeform title",
    )


# ---------- _strip_html -----------------------------------------------------

def test_strip_html_removes_tags:
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_decodes_entities:
    assert _strip_html("Acme &amp; Co &mdash; remote") == "Acme & Co — remote"


def test_strip_html_handles_none_and_empty:
    assert _strip_html(None) == ""
    assert _strip_html("") == ""


def test_strip_html_collapses_whitespace:
    assert _strip_html("Hello\n\n  world\t\n!") == "Hello world !"


# ---------- parse ---------------------------------------------------------

def _payload(**over):
    base = {
        "title":       "Acme Corp: Senior Backend Engineer",
        "link":        "https://weworkremotely.com/remote-jobs/acme-corp-senior-backend-engineer",
        "guid":        "https://weworkremotely.com/remote-jobs/acme-corp-senior-backend-engineer",
        "description": "<p>Build the backend.</p>",
        "pubDate":     "Wed, 16 Apr 2026 12:34:56 +0000",
        "region":      "USA Only",
        "_category":   "remote-programming-jobs",
    }
    base.update(over)
    return base


def test_parse_full_payload:
    s   = WeWorkRemotelyScraper
    raw = s.parse(_payload)
    assert raw is not None
    # The slug at the end of the URL is the native id.
    assert raw.native_id  == "acme-corp-senior-backend-engineer"
    assert raw.company    == "Acme Corp"
    assert raw.title      == "Senior Backend Engineer"
    assert raw.url        == "https://weworkremotely.com/remote-jobs/acme-corp-senior-backend-engineer"
    assert raw.location   == "USA Only"
    assert raw.description == "Build the backend."
    assert raw.posted_at  == "Wed, 16 Apr 2026 12:34:56 +0000"
    assert raw.remote is True


def test_parse_empty_region_defaults_to_remote:
    """Some categories don't include a <region> — fall back to 'Remote'."""
    s   = WeWorkRemotelyScraper
    raw = s.parse(_payload(region=""))
    assert raw.location == "Remote"


def test_parse_falls_back_to_guid_when_link_missing:
    s   = WeWorkRemotelyScraper
    raw = s.parse(_payload(link=""))
    assert raw is not None
    # native_id is derived from the guid in the same way.
    assert raw.native_id == "acme-corp-senior-backend-engineer"


def test_parse_skips_when_no_colon_in_title:
    """No 'Company: Role' structure → skip rather than fabricate."""
    s = WeWorkRemotelyScraper
    assert s.parse(_payload(title="Just a freeform headline")) is None


def test_parse_skips_when_title_missing:
    s = WeWorkRemotelyScraper
    assert s.parse(_payload(title="")) is None


def test_parse_skips_when_link_and_guid_missing:
    s = WeWorkRemotelyScraper
    assert s.parse(_payload(link="", guid="")) is None


def test_parse_skips_when_company_or_role_empty:
    """Edge case: ' : Engineer' or 'Acme: ' — skip."""
    s = WeWorkRemotelyScraper
    assert s.parse(_payload(title=": Engineer")) is None
    assert s.parse(_payload(title="Acme:")) is None
