"""Tests for src/scrapers/working_nomads.py — parse + helpers."""
from scrapers.working_nomads import WorkingNomadsScraper, _strip_html


# ---------- _strip_html -----------------------------------------------------

def test_strip_html_basic:
    assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"


def test_strip_html_decodes_entities:
    assert _strip_html("Acme &amp; Co") == "Acme & Co"


def test_strip_html_handles_empty:
    assert _strip_html("") == ""
    assert _strip_html(None) == ""


# ---------- parse ---------------------------------------------------------

def _payload(**over):
    base = {
        "id":           12345,
        "title":        "Senior Backend Engineer",
        "company_name": "Acme Corp",
        "url":          "https://www.workingnomads.com/jobs/senior-backend-engineer-acme-corp",
        "location":     "Anywhere",
        "category_name": "Programming",
        "tags":         "python, postgres, remote",
        "pub_date":     "2026-04-16T12:34:56",
        "description":  "<p>Build the backend.</p>",
    }
    base.update(over)
    return base


def test_parse_full_payload:
    s   = WorkingNomadsScraper
    raw = s.parse(_payload)
    assert raw is not None
    assert raw.native_id  == "12345"
    assert raw.title      == "Senior Backend Engineer"
    assert raw.company    == "Acme Corp"
    assert raw.url        == "https://www.workingnomads.com/jobs/senior-backend-engineer-acme-corp"
    assert raw.location   == "Anywhere"
    assert raw.posted_at  == "2026-04-16T12:34:56"
    assert raw.description == "Build the backend."
    assert raw.remote is True


def test_parse_blank_location_defaults_to_remote:
    s   = WorkingNomadsScraper
    raw = s.parse(_payload(location=""))
    assert raw.location == "Remote"


def test_parse_falls_back_to_url_slug_for_id:
    """Older WN entries occasionally lack `id`; use URL slug instead."""
    s   = WorkingNomadsScraper
    raw = s.parse(_payload(id=None))
    assert raw is not None
    assert raw.native_id == "senior-backend-engineer-acme-corp"


def test_parse_company_alias:
    """Some endpoints surface 'company' instead of 'company_name'."""
    s   = WorkingNomadsScraper
    raw = s.parse(_payload(company_name=None, company="Acme Corp"))
    assert raw.company == "Acme Corp"


def test_parse_skips_missing_title:
    s = WorkingNomadsScraper
    assert s.parse(_payload(title="")) is None


def test_parse_skips_missing_company:
    s = WorkingNomadsScraper
    assert s.parse(_payload(company_name="")) is None


def test_parse_skips_no_id_and_no_url:
    s = WorkingNomadsScraper
    assert s.parse(_payload(id=None, url="")) is None
