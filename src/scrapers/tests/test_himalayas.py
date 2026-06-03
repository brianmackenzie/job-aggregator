"""Tests for src/scrapers/himalayas.py — parse only."""
from scrapers.himalayas import HimalayasScraper


def test_parse_primary_field_names:
    s = HimalayasScraper
    raw = s.parse({
        "guid": "abc-123",
        "title": "Head of Engineering",
        "companyName": "Acme",
        "applicationLink": "https://himalayas.app/jobs/acme-hoe",
        "pubDate": "2026-04-16T12:00:00Z",
        "locationRestrictions": ["US", "CA"],
        "description": "Lead the eng org",
        "minSalary": 200000,
        "maxSalary": 260000,
    })
    assert raw is not None
    assert raw.native_id == "abc-123"
    assert raw.title == "Head of Engineering"
    assert raw.company == "Acme"
    assert raw.url == "https://himalayas.app/jobs/acme-hoe"
    assert raw.location == "US, CA"
    assert raw.salary_min == 200000
    assert raw.salary_max == 260000
    assert raw.remote is True


def test_parse_fallback_field_names:
    """Himalayas has renamed fields a few times; fallbacks must work."""
    s = HimalayasScraper
    raw = s.parse({
        "id": "xyz",
        "title": "VP Product",
        "company_name": "Globex",
        "url": "https://himalayas.app/jobs/globex-vp",
        "postedAt": "2026-04-16",
    })
    assert raw is not None
    assert raw.native_id == "xyz"
    assert raw.company == "Globex"
    assert raw.url == "https://himalayas.app/jobs/globex-vp"


def test_parse_missing_essentials_skips:
    s = HimalayasScraper
    assert s.parse({}) is None
    assert s.parse({"title": "No Company"}) is None
    assert s.parse({"title": "No URL", "companyName": "Acme"}) is None
