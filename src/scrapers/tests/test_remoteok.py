"""Tests for src/scrapers/remoteok.py. Unit-tests parse only — no HTTP."""
from scrapers.remoteok import RemoteOKScraper


def test_parse_full_payload:
    s = RemoteOKScraper
    raw = s.parse({
        "id": "123456",
        "position": "Senior Engineer",
        "company": "Acme",
        "url": "https://remoteok.com/l/123456",
        "description": "Do cool things.",
        "location": "Remote",
        "tags": ["python", "senior"],
        "salary_min": 180000,
        "salary_max": 220000,
        "epoch": 1776340800,
    })
    assert raw is not None
    assert raw.native_id == "123456"
    assert raw.title == "Senior Engineer"
    assert raw.company == "Acme"
    assert raw.url == "https://remoteok.com/l/123456"
    assert raw.salary_min == 180000
    assert raw.salary_max == 220000
    assert raw.remote is True
    assert raw.posted_at == "1776340800"


def test_parse_missing_title_skips:
    s = RemoteOKScraper
    assert s.parse({"id": "1", "company": "Acme"}) is None


def test_parse_missing_company_skips:
    s = RemoteOKScraper
    assert s.parse({"id": "1", "position": "Engineer"}) is None


def test_parse_fallback_date_field:
    """When `epoch` is missing, fall back to `date`."""
    s = RemoteOKScraper
    raw = s.parse({
        "id": "1",
        "position": "Eng",
        "company": "Acme",
        "url": "https://example.com",
        "date": "2026-04-16T12:00:00Z",
    })
    assert raw.posted_at == "2026-04-16T12:00:00Z"
